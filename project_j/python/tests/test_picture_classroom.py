"""Story pictures and picture questions in class (ADR-0026): what the page gets, what it may
fetch and report, and what the attempt record and evaluation say the student saw."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, correct_response, deliver_all, read_story
from test_picture_requests import course as picture_course
from test_picture_resources import OPTION_IDS, confirm, lesson

from project_j.application.configuration import Settings
from project_j.classroom.harness import DeliveryFeedback
from project_j.classroom.responses import listed, option_pictures
from project_j.evaluation.assessment import evaluation_request
from project_j.interface.api import create_app
from project_j.platform.models.contracts import solid_png
from project_j.preparation.lessons import confirm_resources

STORY_IDS = ["cup-scene", "kitchen-motion"]


def confirmed_lesson(application, monkeypatch, reject=()):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = list(reject)
    confirm(application, course_ref)
    confirm_resources(application, "r1", LEARNER, course_ref)
    return course_ref


def started(application, course_ref):
    application.start("s1", LEARNER, course_ref, materials_ready=True)
    deliver_all(application.core)
    return application.core


def picture_class(application, monkeypatch, reject=()):
    return started(application, confirmed_lesson(application, monkeypatch, reject))


def illustrations(application):
    return application.student_view(application.core)["illustrations"]


def report(core, action, part, resources, status="completed"):
    return core.delivery(
        DeliveryFeedback(
            delivery_event_id=str(uuid4()),
            delivery_id=f"{action.action_id}:{part}",
            action_id=action.action_id,
            activity_id=core.activity_id,
            task_instance_id=action.task_instance_id,
            sequence=1,
            output_device_id="browser-screen",
            modality="image",
            status=status,
            delivered_range=resources,
            occurred_at=datetime.now(UTC),
        )
    )


def answer_right(application):
    task = application.core.current_task()
    asyncio.run(application.adapter.response(str(uuid4()), correct_response(task)))
    deliver_all(application.core)


def test_illustrations_follow_the_story(application, monkeypatch):
    core = picture_class(application, monkeypatch)
    assert illustrations(application) == []
    core.control(str(uuid4()), "start_reading")
    both = [
        {"resource_id": "cup-scene", "paragraph": 1},
        {"resource_id": "kitchen-motion", "paragraph": 2},
    ]
    assert illustrations(application) == both
    core.control(str(uuid4()), "pause")
    assert illustrations(application) == []
    core.control(str(uuid4()), "resume")
    deliver_all(core)
    core.control(str(uuid4()), "finish_reading")
    deliver_all(core)
    assert core.current_task().task_id == "cup-choice"
    assert illustrations(application) == both


def test_option_pictures_need_every_option():
    body = picture_course()
    task = next(task for task in body.tasks if task.task_id == "cup-choice")
    assert len(option_pictures(task, body.resources)) == 3
    partial = tuple(item for item in body.resources if item.resource_id != "cup-option-2")
    assert option_pictures(task, partial) is None
    other = next(task for task in body.tasks if task.task_id == "cup-light")
    assert option_pictures(other, body.resources) is None


def test_option_pictures_follow_the_shuffled_options():
    body = picture_course()
    task = next(task for task in body.tasks if task.task_id == "cup-choice")
    ids = option_pictures(task, body.resources)
    by_id = {item.resource_id: item.option for item in body.resources}
    assert [task.choice.options[by_id[name]] for name in ids] == listed(task)
    assert ids != OPTION_IDS  # The display order is shuffled, never the written one.


def test_task_view_carries_pictures_only_when_complete(application, monkeypatch):
    core = picture_class(application, monkeypatch)
    read_story(core)
    view = core.snapshot()["task"]
    assert sorted(view["pictures"]) == OPTION_IDS and len(view["options"]) == 3
    core.control(str(uuid4()), "stop")


def test_a_turned_down_question_shows_words(application, monkeypatch):
    core = picture_class(application, monkeypatch, reject=[set(), {2}, {2}, {2}])
    read_story(core)
    assert "pictures" not in core.snapshot()["task"]
    core.control(str(uuid4()), "stop")


def test_picture_endpoint_serves_only_what_is_on_screen(application, monkeypatch):
    # Confirmed before the test server starts: confirming writes a lot, and the server's own
    # startup work would race it from the server thread (only in tests).
    course_ref = confirmed_lesson(application, monkeypatch)
    with TestClient(create_app(Settings(), application)) as client:
        core = started(application, course_ref)
        student = {"Authorization": f"Bearer {application.token}"}

        def status(name):
            return client.get(f"/api/class/picture/{name}", headers=student).status_code

        assert status("cup-scene") == 404  # The story is not out yet.
        core.control(str(uuid4()), "start_reading")
        picture = client.get("/api/class/picture/cup-scene", headers=student)
        assert picture.status_code == 200 and picture.content == solid_png()
        assert picture.headers["content-security-policy"] == "sandbox"
        assert status("cup-option-0") == 404  # No question yet.
        core.control(str(uuid4()), "finish_reading")
        deliver_all(core)
        assert status("cup-option-0") == 200
        assert status("cup-look") == 404  # Shown with its own question, by the action.
        assert client.get("/api/class/picture/cup-scene").status_code == 401
        core.control(str(uuid4()), "stop")


def test_picture_report_accepts_story_and_option_pictures(application, monkeypatch):
    core = picture_class(application, monkeypatch)
    read_story(core)
    question = core.actions[core.presented]
    before = (core.mode, core.wait_kind, core.pending, core.revision)
    report(core, question, "story", STORY_IDS)
    report(core, question, "options", OPTION_IDS)
    assert (core.mode, core.wait_kind, core.pending, core.revision) == before
    seen = core.seen[question.task_instance_id]
    assert {name: seen[name] for name in STORY_IDS} == dict.fromkeys(STORY_IDS, "story")
    assert {name: seen[name] for name in OPTION_IDS} == dict.fromkeys(OPTION_IDS, "option")
    with pytest.raises(ValueError, match="visual"):
        report(core, question, "visual", ["cup-look"])  # Another question's picture.
    core.control(str(uuid4()), "stop")


def test_picture_report_refuses_a_hidden_story_picture(application, monkeypatch):
    core = picture_class(application, monkeypatch)
    core.control(str(uuid4()), "start_reading")
    core.control(str(uuid4()), "pause")
    with pytest.raises(ValueError, match="visual"):
        report(core, core.actions[core.presented], "story", ["cup-scene"])
    core.control(str(uuid4()), "stop")


def test_attempt_records_story_option_and_question_pictures(application, monkeypatch):
    core = picture_class(application, monkeypatch)
    read_story(core)
    question = core.actions[core.presented]
    report(core, question, "story", STORY_IDS)
    report(core, question, "options", OPTION_IDS)
    answer_right(application)
    first = core.attempts[-1]
    assert first["task_id"] == "cup-choice"
    assert {(v["resource_id"], v["stage"]) for v in first["visuals"]} == {
        *((name, "story") for name in STORY_IDS),
        *((name, "option") for name in OPTION_IDS),
    }
    look = core.actions[core.presented]
    assert look.visuals == ("cup-look",)
    report(core, look, "visual", ["cup-look"])
    answer_right(application)
    second = core.attempts[-1]
    assert second["task_id"] == "cup-light"
    assert second["visuals"] == [
        {"resource_id": "cup-look", "stage": "question", "picture_question": True}
    ]
    fact = {"end": {}, "attempts": core.attempts, "limitations": []}
    request = evaluation_request(fact, core.course.model_dump(mode="json"), [], None)
    assert request.context["attempts"][-1]["visuals"] == second["visuals"]
    assert "story" in request.instructions and "option" in request.instructions
    assert "picture_question" in request.instructions
    core.control(str(uuid4()), "stop")


def test_classroom_picture_logs(application, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = picture_class(application, monkeypatch)
    assert "illustrations=2 option_pictures=3" in caplog.text
    read_story(core)
    report(core, core.actions[core.presented], "story", ["cup-scene"], "partial")
    assert f"class pictures activity={core.activity_id} part=story shown=1 status=partial" in (
        caplog.text
    )
    for secret in ("What cup-", "Which one shows", "watercolor"):
        assert secret not in caplog.text
    core.control(str(uuid4()), "stop")
