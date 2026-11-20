"""Deadlines and model bindings read from config.json and changed from the settings page."""

import json
import logging

import pytest
from pydantic import BaseModel

from project_j.platform.models.config import ModelFiles
from project_j.platform.models.contracts import (
    DEFAULT_DEADLINES,
    AdapterReply,
    Binding,
    ModelRequest,
    ProbeItem,
)

LOW = Binding(adapter="simulated", model="simulated-jane", effort="low")


class Out(BaseModel):
    ok: bool = True


@pytest.fixture
def files(tmp_path):
    return ModelFiles(tmp_path / "models")


def test_request_carries_a_key_or_seconds_but_not_both():
    ModelRequest("evaluation", "x", {}, Out, deadline_key="evaluation")
    ModelRequest("classroom_judgment", "x", {}, Out, deadline_seconds=4.5)
    with pytest.raises(ValueError):
        ModelRequest("evaluation", "x", {}, Out)
    with pytest.raises(ValueError):
        ModelRequest("evaluation", "x", {}, Out, deadline_seconds=1, deadline_key="evaluation")


def test_deadlines_default_when_the_file_or_section_is_missing(files):
    assert files.deadlines() == DEFAULT_DEADLINES
    files.configure(LOW, {})
    assert files.deadlines() == DEFAULT_DEADLINES


def test_invalid_values_fall_back_per_key_and_log_once(files, caplog):
    caplog.set_level(logging.WARNING, logger="project_j")
    files.configure(LOW, {})
    data = json.loads(files.path("config.json").read_text())
    data["deadlines"] = {
        "evaluation": "600",
        "planning": -1,
        "vocabulary": 3601,
        "classroom_judgment": True,
        "preparation_image": 45,
    }
    files.path("config.json").write_text(json.dumps(data))
    first = files.deadlines()
    files.deadlines()
    assert first == DEFAULT_DEADLINES | {"preparation_image": 45}
    assert caplog.text.count("deadline key=evaluation source=default reason=invalid") == 1
    assert "key=planning" in caplog.text and "key=vocabulary" in caplog.text
    assert "key=classroom_judgment" in caplog.text


def test_saving_deadlines_keeps_bindings_and_the_digest(files):
    files.configure(LOW, {})
    before = files.digest({"simulated": "1"})
    files.configure_deadlines(DEFAULT_DEADLINES | {"evaluation": 900})
    assert files.deadlines()["evaluation"] == 900
    assert files.bindings()["evaluation"] == LOW
    assert files.default() == LOW
    assert files.digest({"simulated": "1"}) == before
    medium = Binding(adapter="simulated", model="simulated-jane", effort="medium")
    files.configure(LOW, {"evaluation": medium})
    assert files.deadlines()["evaluation"] == 900  # configure keeps the section
    assert files.bindings()["evaluation"] == medium


def test_default_binding_needs_a_file(files):
    with pytest.raises(ValueError):
        files.default()


# --- The model service reads the seconds at each call (Task 2) ---

import asyncio  # noqa: E402

from project_j.platform.models.init import probe_all  # noqa: E402
from project_j.platform.models.registry import load_service  # noqa: E402
from project_j.platform.models.service import ModelService  # noqa: E402


def probed(files):
    """A configured, probed service on the simulated adapter."""
    files.configure(LOW, {})
    asyncio.run(probe_all(files))
    return load_service(files)


def test_run_waits_the_seconds_the_file_says(files):
    service = probed(files)
    files.configure_deadlines(DEFAULT_DEADLINES | {"evaluation": 1})

    async def slow(req, binding):  # Two seconds of "thinking", whatever the request asks.
        await asyncio.sleep(2)
        return AdapterReply(data={"ok": True})

    service.adapters["simulated"].invoke = slow
    request = ModelRequest("evaluation", "x", {"a": 1}, Out, deadline_key="evaluation")
    result = asyncio.run(service.run(request))
    assert result.failure == "timeout" and service.calls[-1]["deadline_seconds"] == 1
    files.configure_deadlines(DEFAULT_DEADLINES | {"evaluation": 5})
    assert asyncio.run(service.run(request)).failure is None  # next call, new value, no restart


def test_simulated_service_uses_defaults_and_is_not_configurable():
    service = ModelService.simulated()
    assert service.deadline("classroom_judgment") == 10
    status = service.status()
    assert status["deadlines"] == DEFAULT_DEADLINES and status["configurable"] is False
    assert status["deadline_defaults"] == DEFAULT_DEADLINES


def test_study_service_reports_file_deadlines(files):
    service = probed(files)
    files.configure_deadlines(DEFAULT_DEADLINES | {"planning": 300})
    assert service.status()["deadlines"]["planning"] == 300 and service.status()["configurable"]


# --- The classroom reads the judgment deadline at each submit (Task 4) ---

from support import deliver_all, read_story, start  # noqa: E402


def test_classroom_deadline_is_read_at_each_submit(application, tmp_path):
    files = ModelFiles(tmp_path / "models")
    probed(files)
    application.models.files = files  # The simulated service, now with a configuration root.

    async def scenario():
        core = await start(application)
        deliver_all(core)
        read_story(application.core)
        application.models.adapters["simulated"].delay_seconds = 2
        files.configure_deadlines(DEFAULT_DEADLINES | {"classroom_judgment": 1})
        failed = await application.adapter.text("slow-1", "a bridge")
        assert failed["ack"] == "failed"
        failure = [e for e in core.events if e["kind"] == "model_failure"][-1]["payload"]
        assert 0 < failure["deadline_seconds"] <= 1
        deliver_all(core)  # Jane's "I could not check that answer" reaches the student.
        files.configure_deadlines(DEFAULT_DEADLINES | {"classroom_judgment": 5})
        accepted = await application.adapter.text("slow-2", "a bridge")
        assert accepted["ack"] == "accepted"  # The next submit waits the new value.

    asyncio.run(scenario())


# --- Settings use cases and endpoints (Task 5) ---

from fastapi.testclient import TestClient  # noqa: E402

from project_j.application.configuration import Settings  # noqa: E402
from project_j.application.coordinator import Coordinator  # noqa: E402
from project_j.application.settings import BUSY  # noqa: E402
from project_j.interface.api import create_app  # noqa: E402
from project_j.platform.models import simulated as sim  # noqa: E402
from project_j.platform.models.contracts import PURPOSES  # noqa: E402
from project_j.platform.storage import SimulationStore  # noqa: E402

MEDIUM = {"adapter": "simulated", "model": "simulated-jane", "effort": "medium", "tier": "default"}
PURPOSES_MEDIUM = dict.fromkeys(PURPOSES, MEDIUM)


def auth(app):
    return {"Authorization": f"Bearer {app.token}"}


@pytest.fixture
def study(tmp_path):
    files = ModelFiles(tmp_path / "project_j/private/models")
    service = probed(files)
    store = SimulationStore(tmp_path / "study", "s1", realm="study")
    app = Coordinator(
        store,
        tmp_path,
        verification={
            "result": "pass",
            "method": "explicit_test_fixture",
            "scope": "simulation_only",
            "source_type": "simulated",
            "qualification": "not_granted",
        },
        models=service,
        onboarding=False,
    )
    try:
        yield app, files
    finally:
        store.close()


def test_deadlines_endpoint_validates_and_takes_effect(study, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), app)) as client:
        post = lambda body: client.post("/api/settings/deadlines", headers=auth(app), json=body)  # noqa: E731
        assert post({"deadlines": DEFAULT_DEADLINES | {"evaluation": 0}}).status_code == 422
        assert post({"deadlines": DEFAULT_DEADLINES | {"evaluation": 3601}}).status_code == 422
        missing = {k: v for k, v in DEFAULT_DEADLINES.items() if k != "planning"}
        assert post({"deadlines": missing}).status_code == 422
        response = post({"deadlines": DEFAULT_DEADLINES | {"evaluation": 900}})
        assert response.status_code == 200 and response.json()["deadlines"]["evaluation"] == 900
        dashboard = client.get("/api/dashboard", headers=auth(app)).json()
        assert dashboard["models"]["deadlines"]["evaluation"] == 900
    assert files.deadlines()["evaluation"] == 900
    assert "settings deadlines saved changed=evaluation" in caplog.text


def test_simulation_refuses_settings(application):
    with TestClient(create_app(Settings(), application)) as client:
        bodies = {
            "deadlines": {"deadlines": DEFAULT_DEADLINES},
            "bindings": {"purposes": PURPOSES_MEDIUM},
        }
        for path, body in bodies.items():
            response = client.post(f"/api/settings/{path}", headers=auth(application), json=body)
            assert response.status_code == 409, path
            assert response.json()["message"] == "Settings are fixed in simulation"
        listed = client.get("/api/settings/models", headers=auth(application))
        assert listed.status_code == 409
        assert listed.json()["message"] == "Settings are fixed in simulation"


def test_bindings_switch_only_after_a_passed_probe(study, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), app)) as client:
        post = lambda body: client.post("/api/settings/bindings", headers=auth(app), json=body)  # noqa: E731
        xhigh = {**PURPOSES_MEDIUM, "evaluation": MEDIUM | {"effort": "xhigh"}}
        assert post({"purposes": xhigh}).status_code == 422  # not offered by the simulated adapter
        partial = {k: v for k, v in PURPOSES_MEDIUM.items() if k != "vocabulary"}
        assert post({"purposes": partial}).status_code == 422
        saved = post({"purposes": PURPOSES_MEDIUM})
        assert saved.status_code == 200 and saved.json()["initialized"]
        assert app.models.bindings["evaluation"].effort == "medium"
    assert files.bindings()["evaluation"].effort == "medium"
    assert load_service(files).reasons("evaluation") == []  # the record fits; a restart keeps it
    assert "settings bindings probe outcome=pass reason_kind=none switched=yes" in caplog.text
    assert "settings bindings saved purposes=" in caplog.text
    assert "evaluation=simulated:simulated-jane:medium:default" in caplog.text


def test_a_refused_setup_keeps_the_current_one(study, monkeypatch, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    original = sim.SimulatedAdapter.report

    def no_medium(self, model, efforts):
        report = original(self, model, efforts)
        items = report.items | {"effort:medium": ProbeItem(status="unsupported")}
        return report.model_copy(update={"items": items})

    monkeypatch.setattr(sim.SimulatedAdapter, "report", no_medium)
    current, record = app.models, files.path("init.json").read_bytes()
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
    assert response.status_code == 409
    message = response.json()["message"]
    assert message.startswith("The new setup did not pass: Planning: Effort medium: not supported")
    assert message.endswith("The current setup stays.")
    assert app.models is current and files.bindings()["evaluation"].effort == "low"
    assert files.path("init.json").read_bytes() == record
    assert "settings bindings probe outcome=refused reason_kind=none switched=no" in caplog.text


def test_probe_failure_keeps_the_current_setup(study, monkeypatch, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")

    async def boom(self, *args, **kwargs):
        raise RuntimeError("secret-provider-text")

    monkeypatch.setattr(sim.SimulatedAdapter, "probe", boom)
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
        assert response.status_code == 409 and "RuntimeError" in response.json()["message"]
        assert "secret-provider-text" not in response.text
    assert app.models.reasons("evaluation") == [] and files.bindings()["evaluation"].effort == "low"
    assert "probe outcome=fail reason_kind=RuntimeError switched=no" in caplog.text
    assert "secret-provider-text" not in caplog.text


def probe_then(app, monkeypatch, step):
    """The real probe, with step() run while it is under way."""
    from project_j.application import settings as use_cases

    real = use_cases.probe_bindings

    async def probing(files, bindings):
        step()
        return await real(files, bindings)

    monkeypatch.setattr(use_cases, "probe_bindings", probing)


def test_work_started_while_probing_stops_the_switch(study, monkeypatch, caplog):
    from project_j.application.settings import STARTED

    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    probe_then(app, monkeypatch, lambda: app.jobs.update({"prepare:x": object()}))
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
    app.jobs.clear()
    assert response.status_code == 409 and response.json()["message"] == STARTED
    assert files.bindings()["evaluation"].effort == "low"
    assert "outcome=pass reason_kind=started switched=no" in caplog.text


def test_deadlines_saved_while_probing_survive_the_switch(study, monkeypatch):
    app, files = study
    longer = DEFAULT_DEADLINES | {"evaluation": 900}
    probe_then(app, monkeypatch, lambda: files.configure_deadlines(longer))
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
    assert response.status_code == 200
    assert files.deadlines()["evaluation"] == 900
    assert files.bindings()["evaluation"].effort == "medium"


def test_a_second_save_waits_for_the_first(study):
    from project_j.application.settings import PROBING

    app, files = study
    app.probing = True  # A first save is still probing its setup.
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
    assert response.status_code == 409 and response.json()["message"] == PROBING
    assert files.bindings()["evaluation"].effort == "low"


def test_in_class_allows_deadlines_but_not_models(application, tmp_path):
    files = ModelFiles(tmp_path / "models")
    probed(files)
    application.models.files = files  # The simulated coordinator becomes configurable.
    with TestClient(create_app(Settings(), application)) as client:
        asyncio.run(start(application))  # The class starts after the server did.
        assert application.core is not None and application.core.mode != "ended"
        saved = client.post(
            "/api/settings/deadlines",
            headers=auth(application),
            json={"deadlines": DEFAULT_DEADLINES | {"evaluation": 700}},
        )
        assert saved.status_code == 200
        refused = client.post(
            "/api/settings/bindings", headers=auth(application), json={"purposes": PURPOSES_MEDIUM}
        )
        assert refused.status_code == 409
        assert refused.json()["message"] == "A class is running; change the model after it ends."
        assert client.get("/api/settings/models", headers=auth(application)).status_code == 409
    assert files.deadlines()["evaluation"] == 700


# --- Review fixes: an unreadable file never stops a class; running AI work keeps its model ---


def test_an_unreadable_config_falls_back_to_defaults_and_logs_once(files, caplog):
    caplog.set_level(logging.WARNING, logger="project_j")
    files.configure(LOW, {})
    files.path("config.json").write_text("{not json")
    assert files.deadlines() == DEFAULT_DEADLINES
    files.deadlines()
    assert caplog.text.count("deadline source=default reason=unreadable") == 1


def test_running_ai_work_keeps_its_model(study):
    app, files = study
    app.jobs["prepare:demand"] = object()  # A background job under way (application/progress.py).
    with TestClient(create_app(Settings(), app)) as client:
        refused = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
        assert refused.status_code == 409
        assert refused.json()["message"] == BUSY
        saved = client.post(
            "/api/settings/deadlines", headers=auth(app), json={"deadlines": DEFAULT_DEADLINES}
        )
        assert saved.status_code == 200  # Waiting times may still change.
    app.jobs.clear()


# --- The settings page picks a model from what each adapter offers now ---


def test_models_endpoint_lists_what_each_adapter_offers(study, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")
    app.jobs["prepare:demand"] = object()  # Listing only reads; running AI work does not stop it.
    with TestClient(create_app(Settings(), app)) as client:
        response = client.get("/api/settings/models", headers=auth(app))
    app.jobs.clear()
    assert response.status_code == 200
    assert response.json() == {"models": {"simulated": ["simulated-jane"]}}
    assert "settings models listed adapter=simulated result=ok count=1 elapsed_ms=" in caplog.text


def test_an_unreadable_model_list_is_left_out(study, monkeypatch, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")

    async def boom(self):
        raise RuntimeError("secret-provider-text")

    monkeypatch.setattr(sim.SimulatedAdapter, "listed", boom)
    with TestClient(create_app(Settings(), app)) as client:
        response = client.get("/api/settings/models", headers=auth(app))
    assert response.status_code == 200 and response.json() == {"models": {}}
    assert "adapter=simulated result=unavailable reason_kind=RuntimeError" in caplog.text
    assert "secret-provider-text" not in caplog.text + response.text


def test_a_model_list_that_hangs_is_left_out(study, monkeypatch, caplog):
    from project_j.application import settings as use_cases

    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")

    async def hang(self):
        await asyncio.sleep(5)

    monkeypatch.setattr(sim.SimulatedAdapter, "listed", hang)
    monkeypatch.setattr(use_cases, "LIST_SECONDS", 0.05)
    with TestClient(create_app(Settings(), app)) as client:
        response = client.get("/api/settings/models", headers=auth(app))
    assert response.json() == {"models": {}}
    assert "adapter=simulated result=unavailable reason_kind=TimeoutError" in caplog.text


def test_saving_bindings_closes_the_old_runtimes(study, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")
    closed = []

    async def aclose():
        closed.append(True)

    app.models.adapters["simulated"].aclose = aclose  # Stands in for a warmed Codex runtime.
    with TestClient(create_app(Settings(), app)) as client:
        saved = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
        assert saved.status_code == 200
        assert closed == [True]
    assert "settings models reloaded closed=1" in caplog.text


def test_saving_bindings_warms_the_new_runtimes(study, monkeypatch):
    app, _ = study
    warmed = []

    async def prewarm(self):
        warmed.append(self)

    monkeypatch.setattr(sim.SimulatedAdapter, "prewarm", prewarm, raising=False)
    with TestClient(create_app(Settings(), app)) as client:
        warmed.clear()  # The server start warmed the adapters it began with.
        saved = client.post(
            "/api/settings/bindings", headers=auth(app), json={"purposes": PURPOSES_MEDIUM}
        )
        assert saved.status_code == 200
        assert warmed == [app.models.adapters["simulated"]]  # once, the new service only


# --- The reference set run from the settings page (application/reference.py) ---

import time  # noqa: E402

from project_j.application import reference  # noqa: E402


def finished(app, name="reference", seconds=10):
    deadline = time.monotonic() + seconds
    while name in app.jobs:
        assert time.monotonic() < deadline, f"{name} still running"
        time.sleep(0.05)


def run_check(client, app, mode):
    return client.post("/api/settings/reference", headers=auth(app), json={"mode": mode})


def test_reference_runs_in_the_background_and_writes_a_report(study, tmp_path, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), app)) as client:
        response = run_check(client, app, "full")
        assert response.status_code == 202 and response.json() == {"job": "reference"}
        finished(app)
        check = client.get("/api/dashboard", headers=auth(app)).json()["judgment_check"]
    [path] = (tmp_path / reference.REPORTS).glob("judgment-ref-*/judgment-reference.json")
    saved = json.loads(path.read_text())
    assert saved["check_key"] == reference.check_key(app.models)
    assert saved["summary"]["total"] == 92 and check["state"] != "not_run"
    assert check["report"]["run_id"] == path.parent.name
    assert "judgment reference run mode=full cases=92 passed=" in caplog.text
    assert "job done name=reference" in caplog.text
    assert "It's on rocks near the sea." not in caplog.text  # no case answers in the log


def test_reference_refusals(study, application, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), application)) as client:
        assert (
            run_check(client, application, "full").json()["message"]
            == "Settings are fixed in simulation"
        )
    with TestClient(create_app(Settings(), app)) as client:
        app.jobs["reference"] = object()  # a second click while the first run goes on
        assert run_check(client, app, "full").json()["message"] == reference.RUNNING
        app.jobs.clear()
        assert run_check(client, app, "rerun").json()["message"] == reference.NOTHING
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(reference, "in_class", lambda app: True)
            assert run_check(client, app, "full").json()["message"] == reference.CLASS_RUNNING
        files.configure(Binding(adapter="simulated", model="simulated-jane", effort="medium"), {})
        app.models = load_service(files)  # changed since the probe: not admitted
        response = run_check(client, app, "full")
        assert response.status_code == 409 and response.json()["message"] == reference.NOT_PROBED
    assert app.models.calls == [] and "reference" not in app.jobs
    assert "refused POST /api/settings/reference 409" in caplog.text


def test_rerun_writes_beside_the_full_report(study, tmp_path):
    app, _ = study
    folder = tmp_path / reference.REPORTS / "judgment-ref-20261119T030000Z"
    folder.mkdir(parents=True)
    failed = {"case_id": "F1-correct", "passed": False, "reasons": ["model_failure:timeout"]}
    full = {
        "captured_at": "2026-11-19T03:00:00Z",
        "check_key": reference.check_key(app.models),
        "reference": {"selected": None},
        "results": [failed | {"text": None}],
        "summary": {"passed": 0, "total": 1, "judgment_ms": {"median": None, "p95": None}},
    }
    (folder / "judgment-reference.json").write_text(json.dumps(full))
    with TestClient(create_app(Settings(), app)) as client:
        assert run_check(client, app, "rerun").status_code == 202
        finished(app)
    rerun = json.loads((folder / "rerun-1.json").read_text())
    assert [r["case_id"] for r in rerun["results"]] == ["F1-correct"]  # once each
    assert rerun["reference"]["selected"] == ["F1-correct"]


def test_class_cannot_start_while_the_check_runs(study, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")
    app.jobs["reference"] = object()
    body = {"request_id": "r1", "learner_id": app.learner_id}
    body["course_ref"] = {"object_id": "course-1", "revision": 1}
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post("/api/course/start", headers=auth(app), json=body)
    assert response.status_code == 409 and response.json()["message"] == reference.CHECKING
    assert list(app.jobs) == ["reference"]
    app.jobs.clear()
    assert "refused POST /api/course/start 409" in caplog.text


def test_dashboard_carries_the_check(study, application):
    app, _ = study
    with TestClient(create_app(Settings(), app)) as client:
        check = client.get("/api/dashboard", headers=auth(app)).json()["judgment_check"]
    assert check["state"] == "not_run" and check["cases"] == 92
    with TestClient(create_app(Settings(), application)) as client:
        dashboard = client.get("/api/dashboard", headers=auth(application)).json()
    assert dashboard["judgment_check"] is None


def test_a_run_that_loses_the_service_writes_no_report(study, tmp_path, monkeypatch, caplog):
    app, _ = study
    caplog.set_level(logging.INFO, logger="project_j")

    async def gone(self, request, binding):
        raise RuntimeError("runtime gone")

    monkeypatch.setattr(sim.SimulatedAdapter, "invoke", gone)
    with TestClient(create_app(Settings(), app)) as client:
        assert run_check(client, app, "full").status_code == 202
        finished(app)
    assert not list((tmp_path / reference.REPORTS).glob("judgment-ref-*"))
    assert app.job_failures["reference"].startswith("The model service stopped answering")
    assert "job failed name=reference error=RunStopped" in caplog.text
