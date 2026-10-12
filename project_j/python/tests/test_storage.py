import json

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from project_j.application.documents import document, identity
from project_j.application.ledger import Request, Step
from project_j.contracts import RevisionRef
from project_j.platform.files import PrivateFiles
from project_j.platform.storage import (
    Conflict,
    Publication,
    SaveKey,
    SimulationStore,
    StorageBlocked,
)


def test_migration_reopen_pragmas_permissions_and_exact_history(application):
    app = application
    state = app.current("fictional-L1", "state")
    assert app.store.path.stat().st_mode & 0o777 == 0o600
    assert app.store.run.stat().st_mode & 0o777 == 0o700
    with app.store.engine.begin() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "delete"
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar() == 2
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "0001_simulation"
        )
    reopened = SimulationStore(app.store.root, app.store.run.name)
    assert reopened.read(state.ref, "fictional-L1") == state
    with pytest.raises(LookupError, match="exact revision"):
        reopened.read(RevisionRef(object_id=state.object_id, revision=99), "fictional-L1")
    reopened.close()


def test_same_request_reused_different_content_scope_and_purpose_conflict(application):
    ledger = application.ledger
    request = Request(
        caller="test",
        request_id="R",
        learner_id="fictional-L1",
        purpose="prepare",
        body={"value": 1},
    )
    original = ledger.accept(request)
    assert ledger.accept(request) == original
    for patch in ({"purpose": "evaluate"}, {"learner_id": "fictional-L2"}, {"body": {"value": 2}}):
        with pytest.raises(Conflict):
            ledger.accept(Request.model_validate(request.model_dump() | patch))
    step = Step(
        step_id="s",
        source_revision=1,
        owner="learning",
        input_digest="abc",
        attempts=1,
        execution="complete",
        save="succeeded",
    )
    assert ledger.checkpoint(original, "fictional-L1", step)[1] == 1
    assert ledger.checkpoint(original, "fictional-L1", step)[1] == 1
    with pytest.raises(Conflict):
        ledger.checkpoint(original, "fictional-L1", step.model_copy(update={"attempts": 2}), 1)


def test_save_replay_conflict_cas_and_raw_database_immutability(application):
    app = application
    old = app.current("fictional-L1", "state")
    key = SaveKey(operation_id="new-state", step_id="state", source_revision=1)
    revised = document(
        "state",
        old.object_id,
        key.operation_id,
        old.learner_id,
        old.body,
        refs=(old.ref,),
        revision=2,
    )
    publication = Publication(
        ref=revised.ref, expected_revision=1, reason="explicit simulation revision"
    )
    receipt = app.store.save(key, old.learner_id, [revised], (publication,), (old.ref,))
    assert app.store.save(key, old.learner_id, [revised], (publication,), (old.ref,)) == receipt
    assert app.store.read(old.ref, old.learner_id) == old
    assert app.store.current(old.object_id, old.learner_id).ref == revised.ref
    with pytest.raises(Conflict):
        app.store.save(
            key,
            old.learner_id,
            [revised.model_copy(update={"body": old.body | {"extra": True}})],
            (publication,),
            (old.ref,),
        )
    candidate = document(
        "state",
        old.object_id,
        "stale-writer",
        old.learner_id,
        old.body,
        refs=(revised.ref,),
        revision=3,
    )
    with pytest.raises(Conflict) as conflict:
        app.store.save(
            SaveKey(operation_id="stale-writer", step_id="state", source_revision=1),
            old.learner_id,
            [candidate],
            (
                Publication(
                    ref=candidate.ref, expected_revision=1, reason="stale simulation writer"
                ),
            ),
        )
    assert conflict.value.current == revised.ref
    assert app.store.current(old.object_id, old.learner_id).ref == revised.ref
    for action in (
        update(app.store.tables["revisions"]).values(digest="corrupt"),
        delete(app.store.tables["revisions"]),
    ):
        with pytest.raises(IntegrityError, match="immutable"):
            with app.store.write() as connection:
                connection.execute(action)


@pytest.mark.parametrize("point", ["before_publish", "before_commit"])
def test_save_unit_rollback_has_no_revision_pointer_reference_or_receipt(application, point):
    app = application
    old = app.current("fictional-L1", "state")
    key = SaveKey(operation_id="rollback", step_id="state", source_revision=1)
    obj = document(
        "state",
        old.object_id,
        key.operation_id,
        old.learner_id,
        old.body,
        refs=(old.ref,),
        revision=2,
    )
    app.store.fail_at = point
    with pytest.raises(OSError):
        app.store.save(
            key,
            old.learner_id,
            [obj],
            (Publication(ref=obj.ref, expected_revision=1, reason="rollback simulation"),),
        )
    assert app.store.receipt(key, old.learner_id) is None
    assert app.store.current(old.object_id, old.learner_id).ref == old.ref
    with pytest.raises(LookupError):
        app.store.read(obj.ref, old.learner_id)


def test_lost_ack_queries_original_receipt_without_duplicate(application):
    app = application
    key = SaveKey(operation_id="lost-ack", step_id="report", source_revision=1)
    obj = document(
        "report",
        identity("lost-ack", "report"),
        key.operation_id,
        "fictional-L1",
        {"status": "checked"},
    )
    app.store.fail_at = "after_commit"
    with pytest.raises(OSError):
        app.store.save(key, "fictional-L1", [obj])
    receipt = app.store.receipt(key, "fictional-L1")
    assert receipt.records == (obj.ref,)
    assert app.store.save(key, "fictional-L1", [obj]) == receipt


def test_reference_and_private_boundaries(application, tmp_path):
    app = application
    old = app.current("fictional-L1", "state")
    with pytest.raises(ValueError, match="cross-learner"):
        app.store.read(old.ref, "fictional-L2")
    app.initialize("fictional-L2")
    l2 = app.current("fictional-L2", "state")
    obj = document(
        "report", "cross-report", "cross-op", "fictional-L1", {"status": "checked"}, refs=(l2.ref,)
    )
    with pytest.raises(ValueError, match="cross-learner"):
        app.store.save(
            SaveKey(operation_id="cross-op", step_id="report", source_revision=1),
            "fictional-L1",
            [obj],
        )
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "family", "run")
    (tmp_path / "simulation-link").symlink_to(app.store.root, target_is_directory=True)
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "simulation-link", "run")
    (app.store.root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        SimulationStore(app.store.root, "escape")
    assert not (tmp_path / "family").exists()


def test_candidate_save_does_not_activate_and_classroom_closes_disk_access(application):
    app = application
    candidate = document("report", "candidate", "candidate-op", "fictional-L1", {"status": "draft"})
    key = SaveKey(operation_id="candidate-op", step_id="report", source_revision=1)
    app.store.save(key, "fictional-L1", [candidate])
    assert app.store.current(candidate.object_id, "fictional-L1") is None
    app.store.active_activity_id = "sim-active"
    with pytest.raises(StorageBlocked):
        app.store.read(candidate.ref, "fictional-L1")
    with pytest.raises(StorageBlocked):
        app.files.backup()
    app.store.active_activity_id = None


def test_export_backup_restore_preserve_objects_assets_and_reject_corruption(application, tmp_path):
    app = application
    package = app.prepare("prepare", "fictional-L1")
    exported = json.loads(app.files.export("fictional-L1").read_text())
    assert exported["qualification"] == "not_granted"
    assert "OPENAI_API_KEY" not in json.dumps(exported)
    backup = app.files.backup()
    restored = PrivateFiles.restore(backup, app.store.root, "restored")
    course_ref = RevisionRef.model_validate(package["course_ref"])
    assert restored.read(course_ref, "fictional-L1") == app.store.read(course_ref, "fictional-L1")
    for material in restored.list_current("fictional-L1", "material"):
        assert PrivateFiles(restored).read_asset(material.body["asset"])
    restored.close()
    with pytest.raises(ValueError, match="new isolated"):
        PrivateFiles.restore(backup, app.store.root, "restored")
    (backup / "project_j.sqlite3").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        PrivateFiles.restore(backup, app.store.root, "bad-backup")
