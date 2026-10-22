import asyncio
import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import answer, complete_story, deliver_all, next_course, read_story, start

from project_j.application.configuration import Settings
from project_j.application.coordinator import Coordinator
from project_j.application.documents import document
from project_j.contracts import RevisionRef, SubmittedInput
from project_j.interface.api import RecordingInput, build_coordinator, create_app, main
from project_j.interface.submission import InputAdapter
from project_j.learning.fixtures import initialize
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


def test_study_store_labels_every_saved_record(tmp_path):
    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    obj = document("state", "state:x", "op1", "learner", {"status": "checked"})
    store.save(KEY, "learner", [obj])
    saved = store.read(obj.ref, "learner")
    assert (saved.realm, saved.source_type) == ("study", "student")
    store.close()


def test_store_root_must_match_its_realm(tmp_path):
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "simulation", "a1", realm="study")
    with pytest.raises(ValueError):
        SimulationStore(tmp_path / "study/simulation", "a1")


def test_simulation_store_refuses_records_of_another_realm(tmp_path):
    store = SimulationStore(tmp_path / "simulation", "r1")
    obj = document("state", "state:x", "op1", "L1", {"status": "checked"}).model_copy(
        update={"realm": "study", "source_type": "student"}
    )
    with pytest.raises(ValueError):
        store.save(KEY, "L1", [obj])
    store.close()


def test_backup_restores_only_into_its_own_realm(tmp_path):
    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    app = Coordinator(store, ROOT, verification=PASS, onboarding=False)
    result = initialize(app, "learner")
    backup = PrivateFiles(store).backup()
    store.close()
    assert '"realm":"study"' in (backup / "manifest.json").read_text()
    with pytest.raises(ValueError):
        PrivateFiles.restore(backup, tmp_path / "simulation", "r2")
    restored = PrivateFiles.restore(backup, tmp_path / "other/study", "a2")
    demand = restored.read(RevisionRef.model_validate(result["demand_ref"]), "learner")
    assert (demand.realm, demand.source_type) == ("study", "student")
    restored.close()


@pytest.fixture
def study(tmp_path):
    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    app = Coordinator(store, ROOT, verification=PASS, onboarding=False)
    initialize(app)
    try:
        yield app
    finally:
        store.close()


def test_study_class_takes_student_inputs_and_seals_student_facts(study):
    core = asyncio.run(start(study))
    assert core.snapshot()["realm"] == "study"
    assert core.snapshot()["source_type"] == "student"
    deliver_all(core)
    read_story(core)
    result = asyncio.run(answer(study, "a bridge"))
    assert result["ack"] == "accepted"
    core.control("stop-1", "stop")
    sealed = study.seal()  # same call the API makes after an end
    fact = study.store.read(sealed.ref, core.learner_id)  # the stored label, not the draft
    assert (fact.realm, fact.source_type) == ("study", "student")


def test_simulation_core_refuses_student_inputs(application):
    core = asyncio.run(start(application))
    deliver_all(core)
    submitted = SubmittedInput(
        **InputAdapter(core).binding(),
        event_id="e1",
        realm="study",
        source_type="student",
        client_sequence=1,
        modality="text",
        text="听到了",
        provider="student",
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


def study_coordinator(tmp_path, scorer_mode, tts_mode):
    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
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


def test_study_entry_builds_real_providers_on_its_own_partition(tmp_path):
    (tmp_path / "project_j").mkdir()
    settings = Settings(realm="study", project_root=tmp_path, run_id="a1")
    coordinator = build_coordinator(settings)
    assert coordinator.store.root.name == "study"
    assert coordinator.learner_id == "learner"
    assert isinstance(coordinator.scorer, LocalScorer)
    assert isinstance(coordinator.synthesizer, LocalSynthesizer)
    assert "simulated" not in coordinator.models.adapters
    coordinator.store.close()


def test_lifespan_starts_and_stops_both_local_processes(tmp_path):
    coordinator = study_coordinator(tmp_path, "ok", "ok")
    settings = Settings(realm="study", project_root=tmp_path)
    with TestClient(create_app(settings, coordinator)):
        assert coordinator.scorer.ready and coordinator.synthesizer.ready
    assert coordinator.scorer.exited and coordinator.synthesizer.exited


def test_a_process_that_fails_to_start_leaves_class_start_blocked(tmp_path):
    coordinator = study_coordinator(tmp_path, "ok", "not_ready")
    settings = Settings(realm="study", project_root=tmp_path)
    with TestClient(create_app(settings, coordinator)) as client:
        assert "Speech process not started" in coordinator.synthesizer.reasons()
        dashboard = client.get(
            "/api/dashboard",
            headers={"Authorization": f"Bearer {coordinator.token}"},
        )
        assert dashboard.status_code == 200 and dashboard.json()["realm"] == "study"


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


def test_study_entry_refuses_simulation_debug_switches():
    with pytest.raises(SystemExit) as stop:
        main(["--study", "--model-delay", "1"])
    assert stop.value.code == 2


def limitations(value):
    """Every limitations text anywhere in a stored body."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "limitations":
                yield from (item if isinstance(item, list) else [item])
            else:
                yield from limitations(item)
    elif isinstance(value, list):
        for item in value:
            yield from limitations(item)


def test_study_limitations_name_no_role(study):
    asyncio.run(complete_story(study))
    texts = [text for doc in study.store.list_current("learner") for text in limitations(doc.body)]
    assert texts
    for text in texts:
        for word in ("儿童", "孩子", "家长", "模拟", "child", "parent", "Simulat", "simulat"):
            assert word not in text, text


def test_study_prepares_the_next_course_after_feedback(study):
    core, fact = asyncio.run(complete_story(study))
    activity = fact.body["activity_id"]
    study.feedback("fb1", core.learner_id, activity, "无额外补充", [])
    result = asyncio.run(next_course(study, "next1", core.learner_id, activity))
    state = study.store.read(
        RevisionRef.model_validate(result["adoption"]["state_ref"]), core.learner_id
    )
    assert (state.realm, state.source_type) == ("study", "student")


def test_study_state_wording_is_not_simulated(study):
    core, fact = asyncio.run(complete_story(study))
    activity = fact.body["activity_id"]
    study.feedback("fb1", core.learner_id, activity, "无额外补充", [])
    asyncio.run(next_course(study, "next1", core.learner_id, activity))
    for item in study.current(core.learner_id, "state").body["dimensions"]:
        if item["observed_at"]:
            assert "simulated" not in item["task_scope"] + item["material_condition"]


def test_study_starts_at_onboarding(tmp_path):
    from project_j.application.documents import identity
    from project_j.application.progress import next_step

    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    app = Coordinator(store, ROOT, verification=PASS)
    try:
        initialize(app)
        assert next_step(app, "learner")["kind"] == "lexile"
        assert store.current(identity("learner", "demand"), "learner") is None
    finally:
        store.close()


def test_onboarding_switch_is_for_simulation_only():
    with pytest.raises(SystemExit) as stop:
        main(["--study", "--onboarding"])
    assert stop.value.code == 2
