import logging
import os

import pytest

from project_j.application.documents import document
from project_j.platform.reset import main, reset_study
from project_j.platform.storage import SaveKey, SimulationStore, hold_run

KEY = SaveKey(operation_id="op1", step_id="s", source_revision=1)


def stored(root, run_id, realm="study"):
    store = SimulationStore(root, run_id, realm=realm)
    store.save(KEY, "learner", [document("state", "state:x", "op1", "learner", {"a": 1})])
    store.close()


@pytest.fixture
def spaces(tmp_path):
    study = tmp_path / "project_j/private/study"
    stored(study, "run1")
    stored(study, "run2")
    stored(tmp_path / "project_j/test/simulation", "run1", realm="simulation")
    return tmp_path


def test_reset_removes_only_the_study_run(spaces):
    removed = reset_study(spaces, "run1")
    assert removed > 0
    assert not (spaces / "project_j/private/study/run1").exists()
    assert (spaces / "project_j/private/study/run2").exists()
    assert (spaces / "project_j/test/simulation/run1").exists()


def test_reset_refuses_an_unknown_or_invalid_run(spaces):
    with pytest.raises(FileNotFoundError):
        reset_study(spaces, "missing")
    with pytest.raises(ValueError):
        reset_study(spaces, "../run2")


def test_reset_refuses_while_the_service_runs(spaces):
    service = hold_run(spaces / "project_j/private/study/run1")
    try:
        with pytest.raises(RuntimeError):
            reset_study(spaces, "run1")
    finally:
        os.close(service)
    assert (spaces / "project_j/private/study/run1").exists()


def test_reset_cli_needs_the_run_id_typed_back(spaces, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "wrong")
    assert main(["--run-id", "run1", "--root", str(spaces)]) == 1
    assert (spaces / "project_j/private/study/run1").exists()
    monkeypatch.setattr("builtins.input", lambda _: "run1")
    assert main(["--run-id", "run1", "--root", str(spaces)]) == 0
    assert not (spaces / "project_j/private/study/run1").exists()


def test_reset_logs_counts_only(spaces, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    removed = reset_study(spaces, "run1")
    assert f"reset run=run1 files={removed}" in caplog.text
