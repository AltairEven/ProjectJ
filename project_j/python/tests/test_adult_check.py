import asyncio
import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import answer, complete_story, deliver_all, start

from project_j.application.configuration import Settings
from project_j.application.coordinator import Coordinator
from project_j.application.documents import document
from project_j.contracts import RevisionRef, SubmittedInput
from project_j.interface.api import RecordingInput, build_coordinator, create_app, main
from project_j.interface.submission import InputAdapter
from project_j.platform.files import PrivateFiles
from project_j.platform.models.service import ModelService
from project_j.platform.speech.local import LocalScorer
from project_j.platform.storage import SaveKey, SimulationStore
from project_j.platform.tts.local import LocalSynthesizer

ROOT = Path(__file__).resolve().parents[3]
PASS = {
    "result": "pass",
    "method": "explicit_test_fixture",
    "scope": "simulation_only",
    "source_type": "simulated",
    "qualification": "not_granted",
}
KEY = SaveKey(operation_id="op1", step_id="s", source_revision=1)
FIXTURES = Path(__file__).parent / "fixtures"


def test_adult_store_labels_every_saved_record(tmp_path):
    store = SimulationStore(tmp_path / "private/adult_checks", "a1", realm="adult_check")
    obj = document("state", "state:x", "op1", "adult-A1", {"status": "checked"})
    store.save(KEY, "adult-A1", [obj])
    saved = store.read(obj.ref, "adult-A1")
    assert (saved.realm, saved.source_type) == ("adult_check", "adult")
    store.close()


def test_store_root_must_match_its_realm(tmp_path):
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "simulation", "a1", realm="adult_check")
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "adult_checks/simulation", "a1")


def test_simulation_store_refuses_records_of_another_realm(tmp_path):
    store = SimulationStore(tmp_path / "simulation", "r1")
    obj = document("state", "state:x", "op1", "L1", {"status": "checked"}).model_copy(
        update={"realm": "adult_check", "source_type": "adult"}
    )
    with pytest.raises(ValueError):
        store.save(KEY, "L1", [obj])
    store.close()


def test_backup_restores_only_into_its_own_realm(tmp_path):
    store = SimulationStore(tmp_path / "private/adult_checks", "a1", realm="adult_check")
    app = Coordinator(store, ROOT, verification=PASS)
    result = app.initialize("adult-A1")
    backup = PrivateFiles(store).backup()
    store.close()
    assert '"realm":"adult_check"' in (backup / "manifest.json").read_text()
    with pytest.raises(ValueError):
        PrivateFiles.restore(backup, tmp_path / "simulation", "r2")
    restored = PrivateFiles.restore(backup, tmp_path / "other/adult_checks", "a2")
    demand = restored.read(RevisionRef.model_validate(result["demand_ref"]), "adult-A1")
    assert (demand.realm, demand.source_type) == ("adult_check", "adult")
    restored.close()


@pytest.fixture
def adult(tmp_path):
    store = SimulationStore(tmp_path / "private/adult_checks", "a1", realm="adult_check")
    app = Coordinator(store, ROOT, verification=PASS)
    app.initialize()
    try:
        yield app
    finally:
        store.close()


def test_adult_class_takes_adult_inputs_and_seals_adult_facts(adult):
    core = asyncio.run(start(adult))
    assert core.snapshot()["realm"] == "adult_check"
    assert core.snapshot()["source_type"] == "adult"
    deliver_all(core)
    result = asyncio.run(answer(adult, "听到了", "confirmation"))
    assert result["ack"] == "accepted"
    core.control("stop-1", "stop")
    sealed = adult.seal()  # same call the API makes after an end
    fact = adult.store.read(sealed.ref, core.learner_id)  # the stored label, not the draft
    assert (fact.realm, fact.source_type) == ("adult_check", "adult")


def test_simulation_core_refuses_adult_inputs(application):
    core = asyncio.run(start(application))
    deliver_all(core)
    submitted = SubmittedInput(
        **InputAdapter(core).binding(),
        event_id="e1",
        realm="adult_check",
        source_type="adult",
        client_sequence=1,
        modality="text",
        text="听到了",
        provider="adult_tester",
        intent="confirmation",
        completion_trigger="explicit",
    )
    with pytest.raises(ValueError):
        asyncio.run(core.submit(submitted))


def test_recording_input_fits_300_seconds_of_16k_wav():
    audio = base64.b64encode(bytes(300 * 16000 * 2 + 44)).decode()
    RecordingInput(
        event_id="e1",
        audio_base64=audio,
        input_started_ms=0,
        input_ended_ms=1,
        recording_ms=300_000,
    )


def adult_coordinator(tmp_path, scorer_mode, tts_mode):
    store = SimulationStore(tmp_path / "private/adult_checks", "a1", realm="adult_check")
    return Coordinator(
        store,
        ROOT,
        verification=PASS,
        models=ModelService.simulated(),
        scorer=LocalScorer(
            tmp_path, [sys.executable, str(FIXTURES / "fake_scorer.py"), scorer_mode]
        ),
        synthesizer=LocalSynthesizer(
            tmp_path, [sys.executable, str(FIXTURES / "fake_tts.py"), tts_mode]
        ),
    )


def test_adult_entry_builds_real_providers_on_its_own_partition(tmp_path):
    (tmp_path / "project_j").mkdir()
    settings = Settings(realm="adult_check", project_root=tmp_path, run_id="a1")
    coordinator = build_coordinator(settings)
    assert coordinator.store.root.name == "adult_checks"
    assert isinstance(coordinator.scorer, LocalScorer)
    assert isinstance(coordinator.synthesizer, LocalSynthesizer)
    assert "simulated" not in coordinator.models.adapters
    coordinator.store.close()


def test_lifespan_starts_and_stops_both_local_processes(tmp_path):
    coordinator = adult_coordinator(tmp_path, "ok", "ok")
    settings = Settings(realm="adult_check", project_root=tmp_path)
    with TestClient(create_app(settings, coordinator)):
        assert coordinator.scorer.ready and coordinator.synthesizer.ready
    assert coordinator.scorer.exited and coordinator.synthesizer.exited


def test_a_process_that_fails_to_start_leaves_class_start_blocked(tmp_path):
    coordinator = adult_coordinator(tmp_path, "ok", "not_ready")
    settings = Settings(realm="adult_check", project_root=tmp_path)
    with TestClient(create_app(settings, coordinator)) as client:
        assert "合成进程未启动" in coordinator.synthesizer.reasons()
        dashboard = client.get(
            "/api/parent/dashboard",
            headers={"Authorization": f"Bearer {coordinator.parent_token}"},
        )
        assert dashboard.status_code == 200 and dashboard.json()["realm"] == "adult_check"


def test_workbench_link_is_printed_once_the_service_has_started(application, capsys):
    # Printed earlier, it showed before the local models had loaded; unflushed, a redirected
    # log did not show it at all.
    app = create_app(Settings(), application, announce=("工作台：http://127.0.0.1:8765/",))
    assert capsys.readouterr().out == ""
    with TestClient(app):
        assert "工作台：http://127.0.0.1:8765/" in capsys.readouterr().out


def test_service_start_prewarms_model_runtimes(application):
    class Warmable:
        warmed = False

        async def prewarm(self):
            self.warmed = True

    application.models.adapters["warmable"] = warmable = Warmable()
    with TestClient(create_app(Settings(), application)):
        assert warmable.warmed


def test_adult_entry_refuses_simulation_debug_switches():
    with pytest.raises(SystemExit) as stop:
        main(["--adult-check", "--model-delay", "1"])
    assert stop.value.code == 2


def test_adult_check_limitations_do_not_say_simulated(tmp_path):
    store = SimulationStore(tmp_path / "private/adult_checks", "a1", realm="adult_check")
    app = Coordinator(store, ROOT, verification=PASS)
    app.initialize()
    try:
        core, fact = asyncio.run(complete_story(app))
        assert fact.body["limitations"] == ["成人实际检查活动，不能作为儿童学习证据。"]
        for attempt in fact.body["attempts"]:
            assert all(
                "成人实际检查" in item and "模拟" not in item for item in attempt["limitations"]
            )
    finally:
        store.close()


def test_adult_check_prepares_the_next_course_after_feedback(adult):
    core, fact = asyncio.run(complete_story(adult))
    activity = fact.body["activity_id"]
    adult.feedback("fb1", core.learner_id, activity, "无额外补充", [])
    result = asyncio.run(adult.next_course("next1", core.learner_id, activity))
    state = adult.store.read(
        RevisionRef.model_validate(result["adoption"]["state_ref"]), core.learner_id
    )
    assert (state.realm, state.source_type) == ("adult_check", "adult")


def test_adult_state_wording_is_not_simulated(adult):
    core, fact = asyncio.run(complete_story(adult))
    activity = fact.body["activity_id"]
    adult.feedback("fb1", core.learner_id, activity, "无额外补充", [])
    asyncio.run(adult.next_course("next1", core.learner_id, activity))
    for item in adult.current(core.learner_id, "state").body["dimensions"]:
        if item["observed_at"]:
            assert "模拟" not in item["task_scope"] + item["material_condition"]
