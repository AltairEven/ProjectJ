"""Named files of the student's lessons, drafts, words and books, and idle-boundary
export/backup; no credentials or audio archive (ADR-0027)."""

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from project_j.contracts import Document
from project_j.paths import check_path
from project_j.platform.storage import REALMS, SimulationStore, canonical, digest

log = logging.getLogger(__name__)
# The only trees files are written to inside a run (the student's directory).
LESSONS, DRAFTS, VOCABULARY, BOOKS = "lessons", "drafts", "vocabulary", "books"
TREES = (LESSONS, DRAFTS, VOCABULARY, BOOKS)


def file_stem(name: str) -> str:
    """A resource id or word as a file name: anything but letters, digits, _ and - becomes -."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "item"


def stems(names: list[str]) -> dict[str, str]:
    """File stems in order; a stem already taken gets -2, -3, ..."""
    taken: dict[str, str] = {}
    for name in names:
        stem, n = file_stem(name), 1
        while (candidate := stem if n == 1 else f"{stem}-{n}") in taken.values():
            n += 1
        taken[name] = candidate
    return taken


def private_write(path: Path, content: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{uuid4()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def move_readings(run: Path) -> None:
    """Upgrade (ADR-0035): readings/ becomes books/, and each book still in the old shape gets a
    saved version (it has an EPUB: finished, unpainted pictures failed, no outline under
    discussion) and/or a draft (no EPUB, or not finished, or discussing new chapters). A book
    already sorted out no longer matches, so this runs on every opening and changes it once."""
    old, new = run / "readings", run / BOOKS
    moved = drafts = 0
    if old.is_dir():
        if new.exists():
            log.warning("books migration skipped: readings and books both exist")
        else:
            old.rename(new)
            moved = sum(1 for path in new.iterdir() if path.is_dir())
    for folder in sorted(p for p in new.iterdir() if p.is_dir()) if new.is_dir() else ():
        record_file, draft, pictures = folder / "book.json", folder / "draft", folder / "pictures"
        if (draft / "book.json").is_file() or not record_file.is_file():
            continue  # Already has its draft, or is no book.
        try:
            record = json.loads(record_file.read_bytes())
            if not isinstance(record, dict):
                raise ValueError("not a record")
        except ValueError as error:  # One broken book must not stop the program from opening.
            log.warning("book unreadable book=%s error=%s", folder.name, type(error).__name__)
            continue
        epub = (folder / "book.epub").is_file()
        if epub and record.get("status") == "ready" and not record.get("proposal"):
            continue  # Already a saved version.
        # The draft first (pictures, then the record), so an interruption loses nothing.
        for picture in sorted(pictures.glob("[!.]*")) if pictures.is_dir() else ():
            private_write(draft / "pictures" / picture.name, picture.read_bytes())
        private_write(draft / "book.json", record_file.read_bytes())
        if epub:
            states = record.get("pictures") or {}
            saved = record | {
                "status": "ready",
                "proposal": None,
                "proposal_version": 0,
                "pictures": {n: "failed" if s == "pending" else s for n, s in states.items()},
            }
            private_write(record_file, (json.dumps(saved, indent=2) + "\n").encode())
        else:
            shutil.rmtree(pictures, ignore_errors=True)
            record_file.unlink()
        drafts += 1
    if moved or drafts:
        log.info("books migration books=%d drafts=%d", moved, drafts)


class PrivateFiles:
    def __init__(self, store: SimulationStore):
        self.store = store
        move_readings(store.run)

    def path(self, relative: str) -> Path:
        """A path in one of the run's trees, below its root; anything else is refused."""
        path = check_path(self.store.run, relative)
        parts = path.relative_to(self.store.run.resolve()).parts
        if len(parts) < 2 or parts[0] not in TREES:
            raise ValueError("files live only in lessons/, drafts/, vocabulary/ and books/")
        return path

    def read(self, relative: str) -> bytes | None:
        """A named file's bytes, or None when it does not exist."""
        self.store.ensure_idle()
        path = self.path(relative)
        return path.read_bytes() if path.is_file() else None

    def folders(self, tree: str) -> list[str]:
        """The folder names directly in one tree, sorted."""
        root = self.store.run / tree
        return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []

    def put(self, relative: str, content: bytes | str) -> dict:
        """Write a named file (replacing it unless it already holds these bytes)."""
        self.store.ensure_idle()
        data = content.encode() if isinstance(content, str) else content
        path = self.path(relative)
        checksum = hashlib.sha256(data).hexdigest()
        if not (path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum):
            private_write(path, data)
        relative = path.relative_to(self.store.run.resolve()).as_posix()
        return {"path": relative, "sha256": checksum, "bytes": len(data)}

    def remove(self, relative: str) -> bool:
        self.store.ensure_idle()
        path = self.path(relative)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def remove_tree(self, relative: str) -> int:
        """Remove a directory; the number of files it held."""
        self.store.ensure_idle()
        path = self.path(relative)
        if not path.is_dir():
            return 0
        count = sum(1 for item in path.rglob("*") if item.is_file())
        shutil.rmtree(path)
        return count

    def remove_drafts(self, keep: str) -> list[str]:
        """Remove every course draft but keep; the names removed."""
        self.store.ensure_idle()
        root = self.store.run / DRAFTS
        if not root.is_dir():
            return []
        kept = self.path(keep).name
        removed = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != kept)
        for name in removed:
            self.remove_tree(f"{DRAFTS}/{name}")
        return removed

    def read_asset(self, manifest: dict) -> str:
        return self.read_binary(manifest).decode()

    def read_binary(self, manifest: dict) -> bytes:
        self.store.ensure_idle()
        path = check_path(self.store.run, manifest["path"])
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            content = b""
        if (
            hashlib.sha256(content).hexdigest() != manifest["sha256"]
            or len(content) != manifest["bytes"]
        ):
            raise ValueError("material missing or corrupted")
        return content

    def export(self, learner_id: str) -> Path:
        self.store.ensure_idle()
        with self.store.engine.begin() as connection:
            rows = connection.execute(self.store.tables["revisions"].select()).mappings()
            documents = [Document.model_validate_json(row["payload"]) for row in rows]
        documents = [doc for doc in documents if doc.learner_id in {None, learner_id}]
        for doc in documents:
            self.store.boundary(doc, learner_id)
        target = check_path(self.store.run, f"exports/{uuid4()}.json")
        private_write(
            target,
            canonical(
                {
                    "realm": self.store.realm,
                    "learner_id": learner_id,
                    "qualification": "not_granted",
                    "objects": [d.model_dump(mode="json") for d in documents],
                }
            ).encode(),
        )
        return target

    def backup(self) -> Path:
        self.store.ensure_idle()
        target = check_path(self.store.run, f"backups/{uuid4()}")
        target.mkdir(mode=0o700, parents=True)
        destination = target / "project_j.sqlite3"
        destination.touch(mode=0o600)
        # SQLite's backup API snapshots committed state; all app writes share this idle boundary.
        with self.store.engine.connect() as connection, sqlite3.connect(destination) as backup:
            connection.connection.driver_connection.backup(backup)
        # Every file of the student's trees, as it is: old records may name files since removed.
        manifests = []
        for tree in TREES:
            root = self.store.run / tree
            for path in sorted(root.rglob("*")) if root.is_dir() else ():
                if path.is_file() and not path.name.startswith("."):  # Not a write in progress.
                    relative, data = path.relative_to(self.store.run).as_posix(), path.read_bytes()
                    private_write(check_path(target, relative), data)
                    manifests.append(
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "bytes": len(data),
                        }
                    )
        private_write(
            target / "manifest.json",
            canonical(
                {
                    "realm": self.store.realm,
                    "files": manifests,
                    "database_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
            ).encode(),
        )
        return target

    @staticmethod
    def restore(backup: Path, root: Path, run_id: str) -> SimulationStore:
        manifest = json.loads((backup / "manifest.json").read_text())
        realm = next((r for r, (partition, _) in REALMS.items() if partition == root.name), None)
        if realm is None or manifest.get("realm") != realm:
            raise ValueError("a backup restores only into a new run of its own data space")
        destination = root / run_id
        if destination.exists():
            raise ValueError("restore requires a new isolated directory")
        if (
            hashlib.sha256((backup / "project_j.sqlite3").read_bytes()).hexdigest()
            != manifest["database_sha256"]
        ):
            raise ValueError("backup database integrity failure")
        store = SimulationStore(root, run_id, realm)
        store.close()
        shutil.copyfile(backup / "project_j.sqlite3", store.path)
        store.path.chmod(0o600)
        for asset in manifest["files"]:
            source = check_path(backup, asset["path"])
            target = check_path(store.run, asset["path"])
            content = source.read_bytes()
            if (
                hashlib.sha256(content).hexdigest() != asset["sha256"]
                or len(content) != asset["bytes"]
            ):
                raise ValueError("backup material integrity failure")
            private_write(target, content)
        restored = SimulationStore(root, run_id, realm)
        with restored.engine.begin() as connection:
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise ValueError("backup contains broken exact references")
            effective = set(connection.execute(restored.tables["effective"].select()).all())
            current = []
            for row in connection.execute(restored.tables["revisions"].select()).mappings():
                if digest(row["payload"]) != row["digest"]:
                    raise ValueError("backup object integrity failure")
                if (row["object_id"], row["revision"]) in effective:
                    current.append(Document.model_validate_json(row["payload"]))
        files = PrivateFiles(restored)
        for manifest in named_files(current):
            # A draft removed on purpose (confirmed or replaced course) is not checked.
            parts = manifest["path"].split("/")
            if parts[0] != DRAFTS or (restored.run / DRAFTS / parts[1]).is_dir():
                files.read_binary(manifest)
        return restored


def named_files(documents: list[Document]) -> list[dict]:
    """The files current records name: lessons, word audio, and drafts of courses in work."""
    found = []
    for doc in documents:
        if doc.kind == "lesson":
            found += [doc.body["story"], *doc.body["files"].values()]
        elif doc.kind == "vocabulary":
            found += [word["audio"] for word in doc.body.get("words", ()) if word.get("audio")]
        elif doc.kind == "material":
            found.append(doc.body["asset"])
        elif doc.kind == "resource":
            found += [version["asset"] for version in doc.body.get("versions", ())]
    return found
