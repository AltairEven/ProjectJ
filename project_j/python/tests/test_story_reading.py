"""The student reads the story alone, timed, before the first question (ADR-0022)."""

import asyncio
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import (
    LEARNER,
    answer,
    confirmed,
    deliver,
    deliver_all,
    prepare_course,
    read_story,
    start,
)

from project_j.application.configuration import Settings
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.preparation.voice import opening_text


def opened(application):
    core = asyncio.run(start(application))
    deliver_all(core)
    return core


def clocked(core, now):
    core.class_ms = lambda: now[0]


def events(core, kind):
    return [event["payload"] for event in core.events if event["kind"] == kind]


def test_opening_waits_for_start_reading(application):
    core = opened(application)
    assert (core.mode, core.wait_kind, core.task_index) == ("waiting_input", "reading_start", -1)
    assert core.actions[core.presented].text == opening_text(core.course)
    assert not core.story_visible()


def test_reading_time_excludes_pauses_and_the_question_follows_finish(application):
    core, now = opened(application), [1000.0]
    clocked(core, now)
    core.control("s", "start_reading")
    assert core.wait_kind == "reading" and core.story_visible() and core.task_index == -1
    now[0] = 61000.0
    core.control("p", "pause")
    assert not core.story_visible()
    now[0] = 300000.0
    core.control("r", "resume")
    assert (core.mode, core.wait_kind, core.pending) == ("waiting_input", "reading", None)
    now[0] = 320000.0
    core.control("f", "finish_reading")
    assert events(core, "reading_finished") == [
        {"reading_ms": 80000, "word_count": core.story_words, "first_reading": True, "pauses": 1}
    ]
    assert core.task_index == 0 and core.actions[core.pending].kind == "question"
    assert core.story_visible()


def test_typed_input_help_and_skip_are_refused_while_reading(application):
    core = opened(application)
    with pytest.raises(ValueError, match='Press "Start reading"'):
        asyncio.run(answer(application, "Yes, I heard.", "confirmation"))
    with pytest.raises(ValueError, match='Press "Start reading"'):
        asyncio.run(answer(application, "Help me", "help"))
    with pytest.raises(ValueError, match='Press "Start reading"'):
        core.control(str(uuid4()), "skip")
    assert core.task_index == -1 and not events(core, "reading_started")
    core.control(str(uuid4()), "start_reading")
    with pytest.raises(ValueError, match="finished reading"):
        asyncio.run(answer(application, "a bridge"))
    with pytest.raises(ValueError, match="finished reading"):
        core.control(str(uuid4()), "skip")
    assert core.wait_kind == "reading" and not core.attempts


def test_finish_reading_twice_does_not_skip_the_first_question(application):
    core = opened(application)
    read_story(core)
    with pytest.raises(ValueError, match="Nothing is being read"):
        core.control(str(uuid4()), "finish_reading")
    assert core.task_index == 0 and len(events(core, "reading_finished")) == 1


def test_a_repeated_start_event_is_recorded_once(application):
    core = opened(application)
    core.control("same", "start_reading")
    core.control("same", "start_reading")
    assert len(events(core, "reading_started")) == 1


def test_failed_repeat_during_reading_keeps_reading(application):
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.control(str(uuid4()), "repeat")
    deliver(core, status="failed")
    assert (core.mode, core.wait_kind) == ("waiting_input", "reading")
    core.control(str(uuid4()), "finish_reading")
    assert core.task_index == 0


def test_pause_while_jane_repeats_then_resume_returns_to_reading(application):
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.control(str(uuid4()), "repeat")
    assert core.mode == "delivering"
    core.control(str(uuid4()), "pause")
    core.control(str(uuid4()), "resume")
    assert (core.mode, core.wait_kind, core.pending) == ("waiting_input", "reading", None)


def test_stop_while_reading_is_paused_records_unfinished_without_the_pause(application):
    core, now = opened(application), [0.0]
    clocked(core, now)
    core.control(str(uuid4()), "start_reading")
    now[0] = 30000.0
    core.control(str(uuid4()), "pause")
    now[0] = 90000.0
    core.control(str(uuid4()), "stop")
    assert events(core, "reading_unfinished") == [
        {"reading_ms": 30000, "word_count": core.story_words, "first_reading": True, "pauses": 1}
    ]
    kinds = [event["kind"] for event in core.facts()["events"]]
    assert kinds.index("reading_unfinished") < kinds.index("end")


def test_wrap_up_while_reading_records_unfinished_once(application):
    core, now = opened(application), [0.0]
    clocked(core, now)
    core.control(str(uuid4()), "start_reading")
    now[0] = 45000.0
    core.control(str(uuid4()), "close")
    deliver_all(core)
    assert core.wait_kind == "closing_confirmation"
    asyncio.run(answer(application, "Yes, I heard.", "confirmation"))
    assert core.mode == "ended"
    assert [e["reading_ms"] for e in events(core, "reading_unfinished")] == [45000]


def test_reading_logs_carry_numbers_not_the_story(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j.classroom.harness")
    core = opened(application)
    read_story(core)
    assert "event reading_finished" in caplog.text
    assert "reading_ms=" in caplog.text and "word_count=" in caplog.text
    assert core.course.tasks[0].evidence not in caplog.text


def test_student_view_shows_the_story_only_while_open(application):
    core = opened(application)
    story = {ref.object_id for ref in core.course.material_refs}
    assert application.student_view(core)["materials"] == {}
    core.control("s", "start_reading")
    assert set(application.student_view(core)["materials"]) == story
    core.control("p", "pause")
    assert application.student_view(core)["materials"] == {}
    core.control("r", "resume")
    core.control("f", "finish_reading")
    assert set(application.student_view(core)["materials"]) == story


def test_words_and_first_reading_come_from_the_course(application):
    core = opened(application)
    assert 30 <= core.story_words <= 250 and core.first_reading


def test_the_page_starts_reading_through_the_control_route(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        prepared = prepare_course(app, "prepare")
        confirmed(app, prepared)
        app.start("start", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), True)
        deliver_all(app.core)
        reply = client.post(
            "/api/class/control",
            headers={"Authorization": f"Bearer {app.token}"},
            json={"event_id": "r1", "command": "start_reading"},
        )
        assert reply.status_code == 200
        assert reply.json()["wait_kind"] == "reading" and reply.json()["materials"]
        app.core.control("stop", "stop")


def test_skip_cannot_jump_past_the_story_when_the_opening_failed(application):
    core = asyncio.run(start(application))
    deliver(core, status="failed")
    assert core.wait_kind == "delivery_recovery" and core.task_index == -1
    with pytest.raises(ValueError, match="Say it again"):
        core.control(str(uuid4()), "skip")
    assert core.task_index == -1 and not core.story_open
