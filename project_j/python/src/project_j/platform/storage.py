"""SQLite storage for one simulation or study data space. No classroom persistence."""

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import URL, MetaData, create_engine, event, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.pool import NullPool

from project_j.contracts import Contract, Document, RevisionRef
from project_j.paths import check_path


def canonical(value: BaseModel | dict | list) -> str:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# Data space -> (partition directory, source type of everything saved in it).
REALMS = {"simulation": ("simulation", "simulated"), "study": ("study", "student")}


class Conflict(ValueError):
    def __init__(self, message: str, current: RevisionRef | None = None):
        super().__init__(message)
        self.current = current


class StorageBlocked(RuntimeError):
    pass


class SaveKey(Contract):
    realm: Literal["simulation"] = "simulation"
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    source_revision: int = Field(ge=1, strict=True)


class Publication(Contract):
    ref: RevisionRef
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    reason: str = Field(min_length=1)


class SaveReceipt(SaveKey):
    learner_id: str = Field(min_length=1)
    records: tuple[RevisionRef, ...]
    activated: tuple[RevisionRef, ...]
    baseline_refs: tuple[RevisionRef, ...] = ()
    saved_at: AwareDatetime
    save: Literal["succeeded"] = "succeeded"


def check_run_id(run_id: str) -> str:
    if not run_id or len(run_id) > 64 or not all(c.isalnum() or c in "-_" for c in run_id):
        raise ValueError("invalid run identity")
    return run_id


SERVING = "serving.lock"


def hold_run(run: Path) -> int:
    """The serving process holds this lock for its lifetime (the system frees it on any exit);
    the database itself is opened per operation, so an open file does not show a service."""
    fd = os.open(run / SERVING, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise StorageBlocked(f"另一个服务正在使用运行编号 {run.name}。") from None
    return fd


def serving(run: Path) -> bool:
    """Whether a service holds the run; export and reset wait until it stops."""
    try:
        fd = os.open(run / SERVING, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


class SimulationStore:
    def __init__(
        self, root: Path, run_id: str, realm: Literal["simulation", "study"] = "simulation"
    ):
        # Roots are explicit; neither an arbitrary database URL nor a family entry is exposed.
        partition, self.source_type = REALMS[realm]
        self.realm = realm
        if root.name != partition or root.is_symlink():
            raise ValueError(f"storage requires an explicit {partition} root")
        check_run_id(run_id)
        others = {p for p, _ in REALMS.values()} - {partition}
        if any(part in others for part in root.parts):
            raise ValueError(f"{realm} cannot access another private partition")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.parent.name == "private":
            root.parent.chmod(0o700)
        root.chmod(0o700)
        self.root = root.resolve(strict=True)
        self.run = check_path(self.root, run_id, realm)
        if (self.root / run_id).is_symlink():
            raise ValueError("symlink run directory is not allowed")
        self.run.mkdir(mode=0o700, exist_ok=True)
        self.run.chmod(0o700)
        self.path = check_path(self.root, f"{run_id}/project_j.sqlite3", realm)
        if self.path.is_symlink() or (self.run / "project_j.sqlite3").is_symlink():
            raise ValueError("symlink database is not allowed")
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.path.chmod(0o600)
        self.active_activity_id: str | None = None
        self.fail_at: str | None = None  # Explicit one-shot simulation failure injection.
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={"timeout": 0},
            poolclass=NullPool,
        )

        @event.listens_for(self.engine, "do_connect")
        def access_check(dialect, connection_record, args, kwargs):
            self.ensure_idle()
            if self.run.is_symlink() or (self.run / "project_j.sqlite3").is_symlink():
                raise StorageBlocked("private path changed to a symlink")
            if self.path.resolve() != self.path:
                raise StorageBlocked("private path changed")

        @event.listens_for(self.engine, "connect")
        def configure(dbapi, connection_record):
            dbapi.isolation_level = None
            cursor = dbapi.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            self.ensure_idle()
            connection.exec_driver_sql(
                "BEGIN IMMEDIATE" if connection.get_execution_options().get("write") else "BEGIN"
            )

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.write() as connection:
            config.attributes["connection"] = connection
            config.attributes["realm"] = (self.realm, self.source_type)
            command.upgrade(config, "head")
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        self.tables = metadata.tables

    def ensure_idle(self):
        if self.active_activity_id:
            raise StorageBlocked("classroom storage is closed until the fixed end boundary")

    @contextmanager
    def write(self):
        self.ensure_idle()
        with self.engine.connect().execution_options(write=True) as connection:
            with connection.begin():
                yield connection

    def fault(self, point: str, step: str | None = None):
        # "point" fails at any step; "step:point" only at that save step.
        if self.fail_at in {point, f"{step}:{point}"}:
            self.fail_at = None
            raise OSError(f"injected simulation failure at {point}")

    def close(self):
        self.engine.dispose()

    def list_saved(self, learner_id: str, kind: str) -> list[Document]:
        """Latest saved revision of every object of a kind, effective or not."""
        self.ensure_idle()
        with self.engine.begin() as connection:
            revisions, identities = self.tables["revisions"], self.tables["identities"]
            statement = (
                select(revisions.c.object_id, func.max(revisions.c.revision))
                .join(identities, revisions.c.object_id == identities.c.object_id)
                .where(identities.c.learner_id == learner_id, identities.c.kind == kind)
                .group_by(revisions.c.object_id)
            )
            return [
                self.load(
                    connection, RevisionRef(object_id=identity, revision=revision), learner_id
                )
                for identity, revision in connection.execute(statement)
            ]

    @staticmethod
    def boundary(document: Document, learner_id: str):
        if document.learner_id and document.learner_id != learner_id:
            raise ValueError("cross-learner reference")

    def load(self, connection, ref: RevisionRef, learner_id: str | None) -> Document:
        table = self.tables["revisions"]
        row = (
            connection.execute(
                select(table).where(
                    table.c.object_id == ref.object_id,
                    table.c.revision == ref.revision,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError(f"missing exact revision {ref.object_id}/{ref.revision}")
        if digest(row["payload"]) != row["digest"]:
            raise ValueError("stored object integrity failure")
        document = Document.model_validate_json(row["payload"])
        if learner_id is not None:
            self.boundary(document, learner_id)
        return document

    def read(self, ref: RevisionRef, learner_id: str) -> Document:
        self.ensure_idle()
        with self.engine.begin() as connection:
            return self.load(connection, ref, learner_id)

    def current(self, object_id: str, learner_id: str) -> Document | None:
        self.ensure_idle()
        with self.engine.begin() as connection:
            ref = self.head(connection, object_id)
            return self.load(connection, ref, learner_id) if ref else None

    def head(self, connection, object_id: str) -> RevisionRef | None:
        table = self.tables["effective"]
        revision = connection.execute(
            select(table.c.revision).where(
                table.c.object_id == object_id,
            )
        ).scalar_one_or_none()
        return RevisionRef(object_id=object_id, revision=revision) if revision else None

    def receipt(self, key: SaveKey, learner_id: str) -> SaveReceipt | None:
        self.ensure_idle()
        with self.engine.begin() as connection:
            row = self._receipt(connection, key)
            if row is None:
                return None
            result = SaveReceipt.model_validate_json(row["result"])
            if result.learner_id != learner_id:
                raise ValueError("cross-learner receipt")
            for ref in result.records:
                self.load(connection, ref, learner_id)
            return result

    def _receipt(self, connection, key):
        table = self.tables["receipts"]
        return (
            connection.execute(
                select(table).where(
                    table.c.operation_id == key.operation_id,
                    table.c.step_id == key.step_id,
                    table.c.source_revision == key.source_revision,
                )
            )
            .mappings()
            .one_or_none()
        )

    def find_receipt(self, operation_id: str, step_id: str, learner_id: str):
        self.ensure_idle()
        table = self.tables["receipts"]
        with self.engine.begin() as connection:
            rows = list(
                connection.execute(
                    select(table).where(
                        table.c.operation_id == operation_id, table.c.step_id == step_id
                    )
                ).mappings()
            )
        if len(rows) > 1:
            raise Conflict("source revision must be specified for this save-unit query")
        if not rows:
            return None
        result = SaveReceipt.model_validate_json(rows[0]["result"])
        return self.receipt(
            SaveKey(
                operation_id=operation_id, step_id=step_id, source_revision=result.source_revision
            ),
            learner_id,
        )

    def save(
        self,
        key: SaveKey,
        learner_id: str,
        records,
        publications: tuple[Publication, ...] = (),
        baselines: tuple[RevisionRef, ...] = (),
    ):
        self.ensure_idle()
        if any(record.realm not in {"simulation", self.realm} for record in records):
            raise ValueError("record belongs to another realm")
        # The store is the entry's data space: every record saved here carries its label.
        documents = tuple(
            Document.model_validate(
                record.model_dump(mode="json")
                | {
                    "realm": self.realm,
                    "source_type": self.source_type,
                    "dependency_refs": [
                        ref.model_dump() for ref in dict.fromkeys(record.references)
                    ],
                }
            )
            for record in records
        )
        if not documents or len({doc.ref for doc in documents}) != len(documents):
            raise ValueError("a save unit needs distinct records")
        pending = {doc.ref: doc for doc in documents}
        if any(p.ref not in pending for p in publications):
            raise ValueError("publication must belong to this save unit")
        if len({p.ref.object_id for p in publications}) != len(publications):
            raise ValueError("duplicate publication")
        payload_hash = digest(
            canonical(
                {
                    "key": key.model_dump(mode="json"),
                    "learner_id": learner_id,
                    "records": [doc.model_dump(mode="json") for doc in documents],
                    "publications": [p.model_dump(mode="json") for p in publications],
                    "baselines": [ref.model_dump(mode="json") for ref in baselines],
                }
            )
        )
        with self.write() as connection:
            existing = self._receipt(connection, key)
            if existing:
                if existing["payload_digest"] != payload_hash:
                    raise Conflict("same save identity has different content")
                result = SaveReceipt.model_validate_json(existing["result"])
                for ref in result.records:
                    self.load(connection, ref, learner_id)
                return result
            for ref in baselines:
                self.load(connection, ref, learner_id)
                current = self.head(connection, ref.object_id)
                if current != ref:
                    raise Conflict("effective baseline changed", current)
            for doc in documents:
                self.boundary(doc, learner_id)
                if (
                    doc.operation_id != key.operation_id
                    or doc.source_revision != key.source_revision
                ):
                    raise ValueError("record and save identity mismatch")
                for ref in doc.references:
                    target = pending.get(ref) or self.load(connection, ref, learner_id)
                    self.boundary(target, learner_id)
                    if target.learner_id and target.learner_id != doc.learner_id:
                        raise ValueError("a shared object cannot publish a personal dependency")
            receipt = SaveReceipt(
                **key.model_dump(),
                learner_id=learner_id,
                records=tuple(doc.ref for doc in documents),
                activated=tuple(p.ref for p in publications),
                saved_at=datetime.now(UTC),
            )
            receipt = receipt.model_copy(update={"baseline_refs": baselines})
            receipts = self.tables["receipts"]
            connection.execute(
                receipts.insert().values(
                    **key.model_dump(exclude={"realm"}),
                    learner_id=learner_id,
                    payload_digest=payload_hash,
                    result=canonical(receipt),
                )
            )
            identities = self.tables["identities"]
            revisions = self.tables["revisions"]
            refs = self.tables["references"]
            for doc in documents:
                identity = dict(
                    object_id=doc.object_id,
                    kind=doc.kind,
                    owner=doc.owner,
                    learner_id=doc.learner_id,
                    scope=doc.scope,
                )
                connection.execute(insert(identities).values(**identity).on_conflict_do_nothing())
                stored = (
                    connection.execute(
                        select(identities).where(identities.c.object_id == doc.object_id)
                    )
                    .mappings()
                    .one()
                )
                if dict(stored) != identity:
                    raise Conflict("stable object identity cannot change owner or scope")
                content = canonical(doc)
                old = connection.execute(
                    select(revisions.c.digest).where(
                        revisions.c.object_id == doc.object_id,
                        revisions.c.revision == doc.revision,
                    )
                ).scalar_one_or_none()
                if old is not None:
                    raise Conflict("immutable revision already saved by another unit")
                connection.execute(
                    revisions.insert().values(
                        object_id=doc.object_id,
                        revision=doc.revision,
                        payload=content,
                        digest=digest(content),
                    )
                )
                for ref in dict.fromkeys(doc.references):
                    connection.execute(
                        refs.insert().values(
                            object_id=doc.object_id,
                            revision=doc.revision,
                            target_id=ref.object_id,
                            target_revision=ref.revision,
                        )
                    )
            self.fault("before_publish", key.step_id)
            effective = self.tables["effective"]
            for publication in publications:
                doc = pending[publication.ref]
                if doc.body.get("status") not in {"checked", "enabled", "available"}:
                    raise ValueError("draft or unchecked result cannot become effective")
                if publication.expected_revision is None:
                    changed = connection.execute(
                        insert(effective)
                        .values(
                            object_id=doc.object_id,
                            revision=doc.revision,
                        )
                        .on_conflict_do_nothing()
                    ).rowcount
                else:
                    changed = connection.execute(
                        update(effective)
                        .where(
                            effective.c.object_id == doc.object_id,
                            effective.c.revision == publication.expected_revision,
                        )
                        .values(revision=doc.revision)
                    ).rowcount
                if changed != 1:
                    raise Conflict(
                        "effective revision CAS conflict", self.head(connection, doc.object_id)
                    )
            self.fault("before_commit", key.step_id)
        self.fault("after_commit", key.step_id)
        return receipt

    def list_current(self, learner_id: str, kind: str | None = None) -> list[Document]:
        self.ensure_idle()
        with self.engine.begin() as connection:
            effective = self.tables["effective"]
            identities = self.tables["identities"]
            statement = (
                select(effective.c.object_id, effective.c.revision)
                .join(identities, effective.c.object_id == identities.c.object_id)
                .where((identities.c.learner_id == learner_id) | identities.c.learner_id.is_(None))
            )
            if kind:
                statement = statement.where(identities.c.kind == kind)
            result = []
            for identity, revision in connection.execute(statement):
                doc = self.load(
                    connection, RevisionRef(object_id=identity, revision=revision), None
                )
                self.boundary(doc, learner_id)
                result.append(doc)
            return result
