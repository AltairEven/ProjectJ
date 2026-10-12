"""Immutable assets and idle-boundary export/backup; no credentials or audio archive."""

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from project_j.contracts import Document
from project_j.paths import check_path
from project_j.platform.storage import SimulationStore, canonical, digest


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


class PrivateFiles:
    def __init__(self, store: SimulationStore):
        self.store = store

    def asset(self, text: str) -> dict:
        self.store.ensure_idle()
        checksum = digest(text)
        relative = f"assets/{checksum}.txt"
        path = check_path(self.store.run, relative, "simulation")
        if path.exists():
            if digest(path.read_text()) != checksum:
                raise ValueError("fixed material integrity failure")
        else:
            private_write(path, text.encode())
        return {"path": relative, "sha256": checksum, "bytes": len(text.encode())}

    def read_asset(self, manifest: dict) -> str:
        self.store.ensure_idle()
        path = check_path(self.store.run, manifest["path"], "simulation")
        text = path.read_text()
        if digest(text) != manifest["sha256"] or len(text.encode()) != manifest["bytes"]:
            raise ValueError("material missing or corrupted")
        return text

    def export(self, learner_id: str) -> Path:
        self.store.ensure_idle()
        with self.store.engine.begin() as connection:
            rows = connection.execute(self.store.tables["revisions"].select()).mappings()
            documents = [Document.model_validate_json(row["payload"]) for row in rows]
        documents = [
            doc
            for doc in documents
            if doc.learner_id in {None, learner_id}
            and (doc.kind != "household" or learner_id in doc.body.get("applies_to", []))
        ]
        for doc in documents:
            self.store.boundary(doc, learner_id)
        target = check_path(self.store.run, f"exports/{uuid4()}.json", "simulation")
        private_write(
            target,
            canonical(
                {
                    "realm": "simulation",
                    "learner_id": learner_id,
                    "qualification": "not_granted",
                    "objects": [d.model_dump(mode="json") for d in documents],
                }
            ).encode(),
        )
        return target

    def backup(self) -> Path:
        self.store.ensure_idle()
        target = check_path(self.store.run, f"backups/{uuid4()}", "simulation")
        target.mkdir(mode=0o700, parents=True)
        destination = target / "project_j.sqlite3"
        destination.touch(mode=0o600)
        # SQLite's backup API snapshots committed state; all app writes share this idle boundary.
        with self.store.engine.connect() as connection, sqlite3.connect(destination) as backup:
            connection.connection.driver_connection.backup(backup)
        with sqlite3.connect(destination) as backup:
            rows = backup.execute("SELECT payload FROM revisions").fetchall()
        manifests = []
        for (payload,) in rows:
            doc = Document.model_validate_json(payload)
            if doc.kind == "material":
                self.read_asset(doc.body["asset"])
                source = check_path(self.store.run, doc.body["asset"]["path"], "simulation")
                copied = check_path(target, doc.body["asset"]["path"], "simulation")
                private_write(copied, source.read_bytes())
                manifests.append(doc.body["asset"])
        private_write(
            target / "manifest.json",
            canonical(
                {
                    "realm": "simulation",
                    "assets": manifests,
                    "database_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
            ).encode(),
        )
        return target

    @staticmethod
    def restore(backup: Path, simulation_root: Path, run_id: str) -> SimulationStore:
        manifest = json.loads((backup / "manifest.json").read_text())
        if manifest.get("realm") != "simulation":
            raise ValueError("only simulation backups can be restored")
        destination = simulation_root / run_id
        if destination.exists():
            raise ValueError("restore requires a new isolated directory")
        if (
            hashlib.sha256((backup / "project_j.sqlite3").read_bytes()).hexdigest()
            != manifest["database_sha256"]
        ):
            raise ValueError("backup database integrity failure")
        store = SimulationStore(simulation_root, run_id)
        store.close()
        shutil.copyfile(backup / "project_j.sqlite3", store.path)
        store.path.chmod(0o600)
        for asset in manifest["assets"]:
            source = check_path(backup, asset["path"], "simulation")
            target = check_path(store.run, asset["path"], "simulation")
            content = source.read_text()
            if digest(content) != asset["sha256"] or len(content.encode()) != asset["bytes"]:
                raise ValueError("backup material integrity failure")
            private_write(target, content.encode())
        restored = SimulationStore(simulation_root, run_id)
        with restored.engine.begin() as connection:
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise ValueError("backup contains broken exact references")
            for row in connection.execute(restored.tables["revisions"].select()).mappings():
                document = Document.model_validate_json(row["payload"])
                if digest(row["payload"]) != row["digest"]:
                    raise ValueError("backup object integrity failure")
                if document.kind == "material":
                    PrivateFiles(restored).read_asset(document.body["asset"])
        return restored
