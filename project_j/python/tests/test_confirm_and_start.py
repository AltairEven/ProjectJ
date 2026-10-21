"""ADR-0018: one click confirms the course, makes and adopts its resources, then starts."""

import asyncio
import logging
import time

import pytest
from fastapi.testclient import TestClient
from support import LEARNER
from test_preparation_model import science_lesson
from test_preparation_resources import visual_draft

from project_j.application.configuration import Settings
from project_j.application.progress import latest_facts, next_step, retake_course
from project_j.interface.api import create_app
from project_j.platform.models.contracts import AdapterReply
from project_j.platform.storage import Conflict


def preview_of(app, course_ref):
    return next(
        doc
        for doc in app.store.list_current(LEARNER, "preview")
        if doc.body["course_ref"] == course_ref.model_dump()
    )


def end_class(app):
    """Storage opens again once the class has ended and its facts are sealed."""
    app.core.control("stop-test", "stop")
    app.seal()


def fail_picture_once(application, monkeypatch):
    adapter = application.models.adapters["simulated"]
    original, failed = adapter.invoke, []

    async def invoke(request, binding):
        if request.media == "image" and not failed:
            failed.append(request)
            return AdapterReply(data=None, failure="unavailable")
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)


def fail_animation(application, monkeypatch):
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        if request.purpose == "preparation" and request.context.get("kind") == "animation":
            return None  # The simulated model is unavailable for this one.
        return original(request)

    monkeypatch.setattr(adapter, "respond", respond)


def test_one_click_confirms_prepares_resources_and_starts(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    progress = []
    result = asyncio.run(
        application.confirm_and_start(
            "go", LEARNER, course_ref, True, lambda done, total: progress.append((done, total))
        )
    )
    assert result["decision"] == "allow_simulation" and application.core.mode != "ended"
    end_class(application)
    preview = preview_of(application, course_ref)
    assert preview.body["confirmation"]["course_ref"] == course_ref.model_dump()
    generated = [item for item in preview.body["resources"] if "versions" in item]
    assert generated and all(item["adopted_version"] == 1 for item in generated)
    assert progress and progress[-1] == (len(generated), len(generated))


def test_required_resource_failure_blocks_with_retry(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    fail_picture_once(application, monkeypatch)  # the required picture
    before = application.core  # the earlier, ended lesson
    with pytest.raises(Conflict, match="^资源未就绪"):
        asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    assert application.core is before
    result = asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    assert result["decision"] == "allow_simulation"


def test_optional_resource_failure_still_starts(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    fail_animation(application, monkeypatch)
    result = asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    assert result["decision"] == "allow_simulation"
    end_class(application)
    motion = next(
        item
        for item in preview_of(application, course_ref).body["resources"]
        if item["resource_id"] == "cup-motion"
    )
    assert not motion.get("adopted_version")


def test_offscreen_course_needs_materials_ready(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    with pytest.raises(Conflict, match="实物已备好"):
        asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, False))
    assert not preview_of(application, course_ref).body.get("confirmation")


def test_a_build_without_checks_is_refused_before_any_resource(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    application.verification = {"result": "unknown"}
    with pytest.raises(Conflict, match="登记检查"):
        asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    assert not preview_of(application, course_ref).body.get("confirmation")


def test_adopted_versions_are_reused_on_retry(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    fail_picture_once(application, monkeypatch)
    with pytest.raises(Conflict):
        asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    end_class(application)
    for item in preview_of(application, course_ref).body["resources"]:
        if "versions" in item:
            assert len(item["versions"]) == 1, item["resource_id"]


def test_start_logs_resource_counts(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    fail_animation(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    assert "course resources generated=" in caplog.text and "failed=1" in caplog.text


def test_start_route_runs_as_a_job_and_the_class_appears(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    assert next_step(application, LEARNER)["kind"] == "course"
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}
        body = {
            "request_id": "go",
            "learner_id": LEARNER,
            "course_ref": course_ref.model_dump(),
            "materials_ready": True,
        }
        started = client.post("/api/course/start", headers=auth, json=body)
        assert started.status_code == 202
        assert started.json()["job"] == f"start:{course_ref.object_id}:{course_ref.revision}"
        for _ in range(200):
            data = client.get("/api/dashboard", headers=auth).json()
            if data.get("active"):
                break
            time.sleep(0.02)
        assert data["active"] and data["next"]["kind"] == "class"


def test_confirming_saves_and_makes_resources_but_waits_for_the_student(
    application, monkeypatch, caplog
):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}
        body = {"request_id": "c1", "learner_id": LEARNER, "course_ref": course_ref.model_dump()}
        confirmed = client.post("/api/course/confirm", headers=auth, json=body)
        assert confirmed.status_code == 202
        assert confirmed.json()["job"] == f"confirm:{course_ref.object_id}:{course_ref.revision}"
        for _ in range(200):
            data = client.get("/api/dashboard", headers=auth).json()
            if not data["jobs"]:
                break
            time.sleep(0.02)
        assert not data.get("active")  # no class by itself
        before = application.core and application.core.activity_id
        assert application.core is None or application.core.mode == "ended"
        assert data["next"]["kind"] == "course" and data["next"]["subject"]["confirmed"] is True
        generated = [item for item in preview_of(application, course_ref).body["resources"]]
        made = [item for item in generated if "versions" in item]
        assert made and all(item["adopted_version"] == 1 for item in made)
        assert f"job done name=confirm:{course_ref.object_id}" in caplog.text
        start = body | {"request_id": "s1", "materials_ready": True}
        assert client.post("/api/course/start", headers=auth, json=start).status_code == 202
        for _ in range(200):
            if client.get("/api/dashboard", headers=auth).json().get("active"):
                break
            time.sleep(0.02)
        assert application.core.activity_id != before and application.core.mode != "ended"
        assert caplog.text.count("resource generated") == len(made)  # made once, at confirmation
        end_class(application)


def test_a_stopped_class_can_be_taken_again_until_its_evaluation_is_acknowledged(
    application, monkeypatch, caplog
):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    first = application.core.activity_id
    end_class(application)  # the student stopped it
    asyncio.run(application.evaluate("stopped-eval", LEARNER, first, "after_class"))
    step = next_step(application, LEARNER)
    assert step["kind"] == "evaluation" and step["subject"]["retake"] == course_ref.model_dump()
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.confirm_and_start("again", LEARNER, course_ref, True))
    assert application.core.activity_id != first and application.core.mode != "ended"
    assert "attempt=2" in caplog.text
    end_class(application)


def test_only_a_class_that_did_not_end_normally_is_offered_again(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    asyncio.run(application.confirm_and_start("go", LEARNER, course_ref, True))
    end_class(application)
    stopped = latest_facts(application, LEARNER)[0]
    assert retake_course(application, LEARNER, stopped) == course_ref.model_dump()
    finished = stopped.model_copy(update={"body": stopped.body | {"end": {"reason": "normal"}}})
    assert retake_course(application, LEARNER, finished) is None
