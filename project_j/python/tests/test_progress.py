import asyncio
import logging

import pytest
from conftest import ROOT
from support import LEARNER, start, wait_jobs

from project_j.application.coordinator import Coordinator
from project_j.application.documents import document
from project_j.application.progress import advance, next_step, retry
from project_j.platform.storage import Conflict, Publication, SaveKey

PASS = {"result": "pass", "method": "explicit_test_fixture", "scope": "simulation_only"}


def due_plan(app, level="week"):
    plan = app.current(LEARNER, f"plan:{level}")
    obj = document(
        "plan",
        plan.object_id,
        "due-op",
        LEARNER,
        plan.body | {"review_at": "2026-01-01T00:00:00+00:00"},
        refs=plan.dependency_refs,
        revision=plan.revision + 1,
    )
    app.store.save(
        SaveKey(operation_id="due-op", step_id="plan", source_revision=1),
        LEARNER,
        [obj],
        (Publication(ref=obj.ref, expected_revision=plan.revision, reason="测试：复盘到期"),),
    )
    return obj


def test_advance_prepares_when_the_demand_has_no_course(application):
    async def scenario():
        assert next_step(application, LEARNER)["kind"] == "none"
        name = advance(application, LEARNER)
        assert name.startswith("prepare:")
        assert next_step(application, LEARNER)["kind"] == "waiting"
        await wait_jobs(application)
        step = next_step(application, LEARNER)
        assert step["kind"] == "course" and step["subject"]["confirmed"] is False

    asyncio.run(scenario())


def test_prepare_twice_for_one_demand_reuses_the_course(application):
    first = asyncio.run(application.prepare("p1", LEARNER))
    second = asyncio.run(application.prepare("p2", LEARNER))
    assert first["course_ref"] == second["course_ref"]
    assert len(application.store.list_current(LEARNER, "course")) == 1


def test_advance_never_runs_the_same_job_twice(application):
    async def scenario():
        first, second = advance(application, LEARNER), advance(application, LEARNER)
        assert first and second is None and len(application.jobs) == 1
        await wait_jobs(application)
        assert advance(application, LEARNER) is None  # done, and nothing else is due

    asyncio.run(scenario())


def test_no_job_starts_while_a_class_runs(application):
    async def scenario():
        due_plan(application, "stage")  # before: storage is closed once the class runs
        await start(application)
        assert advance(application, LEARNER) is None
        assert next_step(application, LEARNER)["kind"] == "class"

    asyncio.run(scenario())


def test_due_review_starts_by_itself(application):
    async def scenario():
        advance(application, LEARNER)
        await wait_jobs(application)  # the course first
        plan = due_plan(application)
        assert advance(application, LEARNER) == f"review:week:{plan.revision}"
        await wait_jobs(application)
        assert application.store.list_current(LEARNER, "review")
        assert advance(application, LEARNER) is None  # reviewed once per plan revision

    asyncio.run(scenario())


def test_failed_job_waits_for_retry(application):
    async def scenario():
        application.models.adapters["simulated"].fail_next = True
        name = advance(application, LEARNER)
        await wait_jobs(application)
        assert application.job_failures[name].startswith("课程未取得（模型暂不可用）")
        assert advance(application, LEARNER) is None  # no silent retry loop
        step = next_step(application, LEARNER)
        assert step["kind"] == "waiting" and step["subject"]["failed"] == [name]
        assert retry(application, LEARNER, name) == name
        await wait_jobs(application)
        assert name not in application.job_failures
        assert next_step(application, LEARNER)["kind"] == "course"

    asyncio.run(scenario())


def test_advance_resumes_after_restart(application):
    async def interrupted():
        original = application.save_step

        def fail_package(*args, **kwargs):
            if args[2] == "package":
                application.store.fail_at = "before_commit"
            return original(*args, **kwargs)

        application.save_step = fail_package
        name = advance(application, LEARNER)
        await wait_jobs(application)
        assert name in application.job_failures
        return name

    name = asyncio.run(interrupted())
    restarted = Coordinator(application.store, ROOT, verification=PASS)

    async def resumed():
        assert advance(restarted, LEARNER) == name
        await wait_jobs(restarted)
        assert next_step(restarted, LEARNER)["kind"] == "course"
        assert advance(restarted, LEARNER) is None

    asyncio.run(resumed())
    assert len(application.store.list_current(LEARNER, "course")) == 1


def test_job_logs_name_state_and_elapsed(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        application.models.adapters["simulated"].fail_next = True
        name = advance(application, LEARNER)
        await wait_jobs(application)
        retry(application, LEARNER, name)
        await wait_jobs(application)
        return name

    name = asyncio.run(scenario())
    assert f"job start name={name}" in caplog.text
    assert f"job failed name={name} error=PreparationUnavailable elapsed_ms=" in caplog.text
    assert f"job done name={name} elapsed_ms=" in caplog.text


def test_dashboard_shows_the_next_step_and_retry_restarts_a_job(application):
    import time

    from fastapi.testclient import TestClient

    from project_j.application.configuration import Settings
    from project_j.interface.api import create_app

    application.models.adapters["simulated"].fail_next = True
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}

        def until(check):
            for _ in range(200):
                data = client.get("/api/dashboard", headers=auth).json()
                if check(data):
                    return data
                time.sleep(0.02)
            raise AssertionError("background job did not settle")

        first = client.get("/api/dashboard", headers=auth).json()
        assert first["next"]["kind"] == "waiting"  # this very load started the preparation
        name = first["jobs"][0]["name"]
        failed = until(lambda data: data["jobs"] and data["jobs"][0]["state"] == "failed")
        assert failed["jobs"][0]["reason"].startswith("课程未取得（模型暂不可用）")
        body = {"learner_id": LEARNER, "name": name}
        assert client.post("/api/jobs/retry", headers=auth, json=body).json()["started"] == name
        assert until(lambda data: data["next"]["kind"] == "course")["jobs"] == []


REASON = "上节课两处过去时用成了原形"


def waiting_demand(app):
    """A planned demand revision that still waits for the student's ticks."""
    demand = app.current(LEARNER, "demand")
    body = demand.body | {
        "skill_focus": None,
        "focus_suggestions": [{"skill_id": "past_simple", "reason": REASON}],
    }
    obj = document(
        "demand",
        demand.object_id,
        "focus-op",
        LEARNER,
        body,
        refs=demand.dependency_refs,
        sources=demand.source_refs,
        revision=demand.revision + 1,
        occurred_at=demand.occurred_at,
    )
    app.store.save(
        SaveKey(operation_id="focus-op", step_id="demand", source_revision=1),
        LEARNER,
        [obj],
        (Publication(ref=obj.ref, expected_revision=demand.revision, reason="测试：等选择"),),
    )
    return obj


def test_planned_demand_waits_for_the_focus_choice(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        demand = waiting_demand(application)
        step = next_step(application, LEARNER)
        assert step["kind"] == "focus"
        assert len(step["subject"]["options"]) == 13
        assert step["subject"]["suggestions"] == [{"skill_id": "past_simple", "reason": REASON}]
        assert advance(application, LEARNER) is None  # no preparation before the choice
        note = "多练不规则动词"
        first = application.choose_focus("f1", LEARNER, demand.ref, ["past_simple"], note)
        assert application.choose_focus("f1", LEARNER, demand.ref, ["past_simple"], note) == first
        chosen = application.current(LEARNER, "demand")
        assert chosen.revision == demand.revision + 1
        assert chosen.body["skill_focus"] == ["past_simple"] and chosen.body["skill_note"] == note
        with pytest.raises(Conflict):
            application.choose_focus("f2", LEARNER, demand.ref, [], None)
        assert next_step(application, LEARNER)["kind"] == "none"
        assert advance(application, LEARNER).startswith("prepare:")
        await wait_jobs(application)
        assert next_step(application, LEARNER)["kind"] == "course"

    asyncio.run(scenario())
    assert "focus chosen skills=1 note=True" in caplog.text
    assert "不规则" not in caplog.text and REASON not in caplog.text
