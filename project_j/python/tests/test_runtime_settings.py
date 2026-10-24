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
from project_j.platform.storage import SimulationStore  # noqa: E402

MEDIUM = {"adapter": "simulated", "model": "simulated-jane", "effort": "medium", "tier": "default"}
PURPOSES_MEDIUM = {
    p: MEDIUM for p in ("planning", "preparation", "classroom_judgment", "evaluation", "vocabulary")
}


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
            "probe": {},
        }
        for path, body in bodies.items():
            response = client.post(f"/api/settings/{path}", headers=auth(application), json=body)
            assert response.status_code == 409, path
            assert response.json()["message"] == "Settings are fixed in simulation"


def test_bindings_wait_for_a_probe_and_survive_a_restart(study, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), app)) as client:
        post = lambda body: client.post("/api/settings/bindings", headers=auth(app), json=body)  # noqa: E731
        xhigh = {**PURPOSES_MEDIUM, "evaluation": MEDIUM | {"effort": "xhigh"}}
        assert post({"purposes": xhigh}).status_code == 422  # not offered by the simulated adapter
        partial = {k: v for k, v in PURPOSES_MEDIUM.items() if k != "vocabulary"}
        assert post({"purposes": partial}).status_code == 422
        saved = post({"purposes": PURPOSES_MEDIUM})
        assert saved.status_code == 200 and not saved.json()["initialized"]
        assert "changed" in saved.json()["purposes"]["evaluation"]["reasons"][0]
        assert "changed" in load_service(files).reasons("evaluation")[0]  # a restart stays refused
        probe = client.post("/api/settings/probe", headers=auth(app), json={})
        assert probe.status_code == 200 and probe.json()["initialized"]
        assert app.models.bindings["evaluation"].effort == "medium"
    assert "settings bindings saved purposes=" in caplog.text
    assert "evaluation=simulated:simulated-jane:medium:default" in caplog.text
    assert "settings probe outcome=pass elapsed_ms=" in caplog.text


def test_probe_failure_keeps_the_refusal(study, monkeypatch, caplog):
    app, files = study
    caplog.set_level(logging.INFO, logger="project_j")

    async def boom(self, *args, **kwargs):
        raise RuntimeError("secret-provider-text")

    monkeypatch.setattr(sim.SimulatedAdapter, "probe", boom)
    files.configure(Binding(adapter="simulated", model="simulated-jane", effort="medium"), {})
    app.models = load_service(files)
    with TestClient(create_app(Settings(), app)) as client:
        response = client.post("/api/settings/probe", headers=auth(app), json={})
        assert response.status_code == 409 and "RuntimeError" in response.json()["message"]
        assert "secret-provider-text" not in response.text
    assert "changed" in app.models.reasons("evaluation")[0]
    assert files.init_record()["config_digest"] != files.digest(
        {"simulated": sim.SimulatedAdapter.version}
    )
    assert "settings probe outcome=fail reason_kind=RuntimeError" in caplog.text
    assert "secret-provider-text" not in caplog.text


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
        probe = client.post("/api/settings/probe", headers=auth(application), json={})
        assert probe.status_code == 409
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
        assert client.post("/api/settings/probe", headers=auth(app), json={}).status_code == 409
        saved = client.post(
            "/api/settings/deadlines", headers=auth(app), json={"deadlines": DEFAULT_DEADLINES}
        )
        assert saved.status_code == 200  # Waiting times may still change.
    app.jobs.clear()
