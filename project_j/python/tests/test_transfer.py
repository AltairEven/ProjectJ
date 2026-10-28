import io
import logging
import os
import tarfile

import pytest

from project_j.application.documents import document
from project_j.platform.files import PrivateFiles
from project_j.platform.storage import SaveKey, SimulationStore, hold_run
from project_j.platform.transfer import export_run, import_run, main

STUDY = "project_j/private/study"
TEXT = "Mia crossed the paper bridge."


@pytest.fixture
def old(tmp_path):
    """The old computer: one study run with a lesson story, a word's audio and a draft."""
    store = SimulationStore(tmp_path / "old" / STUDY, "me", realm="study")
    files = PrivateFiles(store)
    story = files.put("lessons/lesson-001-2026-10-28/story.md", TEXT)
    audio = files.put("vocabulary/bridge.wav", b"RIFF-word")
    files.put("drafts/2026-10-28-abcd1234/story.r1.md", "Next story")
    key = SaveKey(operation_id="op1", step_id="s", source_revision=1)
    body = {"asset": story, "audio": audio}
    store.save(key, "learner", [document("material", "material:x", "op1", "learner", body)])
    store.close()
    return tmp_path / "old"


def test_a_run_moves_to_another_computer_with_its_records_and_files(old, tmp_path):
    archive = tmp_path / "me.tar.gz"
    assert export_run(old, "me", archive) == 3
    assert archive.stat().st_mode & 0o777 == 0o600  # Only the student can read their records.
    assert not (old / STUDY / "me/backups").exists()  # The intermediate backup is removed.
    new = tmp_path / "new"
    assert import_run(new, "me", archive) == 3
    store = SimulationStore(new / STUDY, "me", realm="study")
    (material,) = store.list_saved("learner", "material")
    files = PrivateFiles(store)
    assert files.read_asset(material.body["asset"]) == TEXT
    assert files.read_binary(material.body["audio"]) == b"RIFF-word"
    assert (store.run / "drafts/2026-10-28-abcd1234/story.r1.md").read_text() == "Next story"
    store.close()


def test_nothing_existing_is_overwritten(old, tmp_path):
    archive = tmp_path / "me.tar.gz"
    export_run(old, "me", archive)
    with pytest.raises(FileExistsError):
        import_run(old, "me", archive)  # The run is already there.
    with pytest.raises(FileExistsError):
        export_run(old, "me", archive)  # So is the archive.
    with pytest.raises(FileNotFoundError):
        export_run(old, "missing", tmp_path / "missing.tar.gz")


def test_a_changed_archive_is_refused_and_leaves_no_half_run(old, tmp_path):
    archive, changed = tmp_path / "me.tar.gz", tmp_path / "changed.tar.gz"
    export_run(old, "me", archive)
    with tarfile.open(archive) as source, tarfile.open(changed, "w:gz") as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if data is not None and "/vocabulary/" in member.name:
                data += b"x"
                member.size = len(data)
            target.addfile(member, io.BytesIO(data) if data is not None else None)
    new = tmp_path / "new"
    with pytest.raises(ValueError, match="import-"):
        import_run(new, "me", changed)
    assert not (new / STUDY / "me").exists()  # The run never holds a half import.
    (kept,) = (new / STUDY).iterdir()  # The incomplete import stays for a look, not deleted.
    assert kept.name.startswith("import-")


def test_export_waits_until_the_service_has_stopped(old, tmp_path):
    archive = tmp_path / "me.tar.gz"
    service = hold_run(old / STUDY / "me")  # What the running service holds for its lifetime.
    try:
        with pytest.raises(RuntimeError):
            export_run(old, "me", archive)
    finally:
        os.close(service)  # The service stops.
    assert not archive.exists()
    assert export_run(old, "me", archive) == 3


def test_transfer_logs_counts_only(old, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    archive, new = tmp_path / "me.tar.gz", tmp_path / "new"
    assert main(["export", "--run-id", "me", str(archive), "--root", str(old)]) == 0
    assert main(["import", "--run-id", "me", str(archive), "--root", str(new)]) == 0
    assert main(["import", "--run-id", "me", str(archive), "--root", str(new)]) == 1
    assert "transfer export run=me files=3 bytes=" in caplog.text
    assert "transfer import run=me files=3" in caplog.text
    assert "transfer import refused FileExistsError" in caplog.text
    assert "Mia" not in caplog.text
