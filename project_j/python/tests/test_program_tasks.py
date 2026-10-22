"""Clicked task types in class (ADR-0020): judged by their own rule, never by a model."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import (
    LEARNER,
    answer,
    complete_story,
    confirmed,
    correct_response,
    deliver_all,
    judgments,
    prepare_course,
    read,
    read_story,
    start,
)

from project_j.application.configuration import Settings
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.platform.storage import Conflict
from project_j.preparation.voice import WRONG_TEXT


async def to_clicked(app):
    """Through the story questions and the reports to the first clicked task."""
    core = await start(app)
    deliver_all(core)
    read_story(app.core)
    await answer(app, "a bridge")
    await answer(app, "She folded it.")
    await answer(app, "I pointed to the folded paper.", "report")
    core.control(str(uuid4()), "return")
    deliver_all(core)
    await answer(app, "The second time the paper was folded and the car did not fall.", "report")
    assert core.current_task().task_id == "choice-first-try"
    return core


def wrong_choice(task) -> dict:
    right = correct_response(task)["choice"]
    return {"choice": (right + 1) % len(task.choice.options)}


async def respond(app, response, intent="answer", event_id=None):
    result = await app.adapter.response(event_id or str(uuid4()), response, intent)
    said = app.core.actions[app.core.pending].text if app.core.pending else None
    deliver_all(app.core)
    return result, said


def test_a_choice_task_is_judged_by_rule_without_a_model_call(application):
    async def scenario():
        core = await to_clicked(application)
        task = core.current_task()
        view = core.snapshot()["task"]
        assert view["type"] == "choice" and view["options"] and "correct" not in view
        calls = len(judgments(application))
        instance = core.task_instance_id
        _, said = await respond(application, wrong_choice(task))
        assert said == f"{WRONG_TEXT} {task.supports[0].text}"
        assert core.wait_kind == "retry" and core.wrong_count[instance] == 1
        _, said = await respond(application, correct_response(task))
        assert said == task.correct_text
        assert core.current_task().task_id == "order-bridge"
        assert len(judgments(application)) == calls  # No model was asked.
        tries = [a for a in core.attempts if a["task_instance_id"] == instance]
        assert [a["judgment"] for a in tries] == ["incorrect", "correct"]
        right = tries[-1]
        assert right["response"] == correct_response(task) and right["type_version"] == 1
        assert right["answer_text"] == task.choice.options[task.choice.correct]
        assert 0 <= right["task_started_ms"] <= right["answered_ms"] <= right["judged_ms"]

    asyncio.run(scenario())


def test_hints_take_the_next_support_and_are_not_attempts(application):
    async def scenario():
        core = await to_clicked(application)
        task, count = core.current_task(), len(core.attempts)
        _, said = await respond(application, None, "help")
        assert said == task.supports[0].text and len(core.attempts) == count
        _, said = await respond(application, None, "help")
        assert said == task.supports[1].text
        _, said = await respond(application, None, "help")  # Help used up: Jane moves on.
        assert said == task.exit_text and core.current_task().task_id == "order-bridge"
        assert len(core.attempts) == count

    asyncio.run(scenario())


def test_wrong_answers_past_the_last_support_exit_the_task(application):
    async def scenario():
        core = await to_clicked(application)
        task = core.current_task()
        for _ in task.supports:
            await respond(application, wrong_choice(task))
        _, said = await respond(application, wrong_choice(task))
        assert said == task.exit_text and core.current_task().task_id == "order-bridge"
        assert any(event["kind"] == "task_exited" for event in core.events)

    asyncio.run(scenario())


def test_text_or_recording_on_a_clicked_task_is_refused(application):
    async def scenario():
        core = await to_clicked(application)
        inputs = len(core.inputs)
        for intent in ("unknown", "help"):
            with pytest.raises(ValueError, match="Answer on the question itself"):
                await application.adapter.text(str(uuid4()), "b", intent)
        with pytest.raises(ValueError, match="Answer on the question itself"):
            await read(application, str(uuid4()), "The paper bends.")
        assert len(core.inputs) == inputs and not core.wrong_count

    asyncio.run(scenario())


def test_a_clicked_answer_elsewhere_is_refused(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        with pytest.raises(ValueError, match='Press "Start reading"'):  # Before the story.
            await application.adapter.response(str(uuid4()), {"choice": 0})
        read_story(core)
        assert core.snapshot()["task"] is None  # A story question has no page view.
        with pytest.raises(ValueError, match="not answered with buttons"):
            await application.adapter.response(str(uuid4()), {"choice": 0})
        assert not core.attempts

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "intent"),
    [
        ({"choice": 9}, "answer"),
        ({"order": [0]}, "answer"),
        (None, "answer"),
        ({"choice": 0}, "help"),
    ],
)
def test_a_malformed_response_is_refused_and_not_counted(application, response, intent):
    async def scenario():
        core = await to_clicked(application)
        attempts, inputs = len(core.attempts), len(core.inputs)
        with pytest.raises(ValueError, match="doesn't fit this question"):
            await application.adapter.response(str(uuid4()), response, intent)
        assert len(core.attempts) == attempts and len(core.inputs) == inputs
        assert not core.wrong_count and core.mode == "waiting_input"

    asyncio.run(scenario())


def test_a_resent_response_replays_and_a_changed_one_conflicts(application):
    async def scenario():
        core = await to_clicked(application)
        task = core.current_task()
        await respond(application, wrong_choice(task), event_id="same")
        attempts = len(core.attempts)
        replay = await application.adapter.response("same", wrong_choice(task))
        assert replay["ack"] == "replayed" and len(core.attempts) == attempts
        with pytest.raises((ValueError, Conflict)):
            await application.adapter.response("same", correct_response(task))
        assert core.wrong_count[core.task_instance_id] == 1

    asyncio.run(scenario())


def test_the_http_route_and_snapshot_carry_the_task_view(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        prepared = prepare_course(app, "prepare")
        confirmed(app, prepared)
        app.start(
            "start",
            LEARNER,
            RevisionRef.model_validate(prepared["course_ref"]),
            materials_ready=True,
        )
        student = {"Authorization": f"Bearer {app.token}"}
        core = app.core
        deliver_all(core)
        view = client.get("/api/class/snapshot", headers=student).json()
        assert view["task"] is None  # The goal, then a story question: nothing to click.

        async def walk():
            read_story(app.core)
            await answer(app, "a bridge")
            await answer(app, "She folded it.")
            await answer(app, "I pointed to the folded paper.", "report")
            core.control(str(uuid4()), "return")
            deliver_all(core)
            await answer(
                app, "The second time the paper was folded and the car did not fall.", "report"
            )

        asyncio.run(walk())
        view = client.get("/api/class/snapshot", headers=student).json()
        assert view["task"]["type"] == "choice" and "correct" not in view["task"]
        task = core.current_task()
        refused = client.post(
            "/api/class/response", headers=student, json={"event_id": "bad", "response": {"x": 1}}
        )
        assert (
            refused.status_code == 400 and "doesn't fit this question" in refused.json()["message"]
        )
        hint = client.post(
            "/api/class/response", headers=student, json={"event_id": "hint", "intent": "help"}
        )
        assert hint.status_code == 200
        assert hint.json()["snapshot"]["pending_action"]["text"] == task.supports[0].text
        deliver_all(core)
        right = client.post(
            "/api/class/response",
            headers=student,
            json={"event_id": "right", "response": correct_response(task)},
        )
        assert right.status_code == 200 and core.attempts[-1]["judgment"] == "correct"
        client.post(
            "/api/class/control", headers=student, json={"event_id": "stop", "command": "stop"}
        )


def test_an_open_task_is_answered_by_text_and_judged_by_the_model(application):
    async def scenario():
        core = await to_clicked(application)
        for _ in range(4):  # The four clicked tasks, answered right.
            await respond(application, correct_response(core.current_task()))
        while core.current_task().purpose == "read_aloud":
            await read(application, str(uuid4()), core.current_task().reference_text)
            deliver_all(core)
        assert core.current_task().task_id == "retell-bridge" and core.snapshot()["task"] is None
        with pytest.raises(ValueError, match="not answered with buttons"):
            await application.adapter.response(str(uuid4()), {"choice": 0})
        calls = len(judgments(application))
        await answer(application, "It fell.")  # Too short for the simulated judge.
        assert core.attempts[-1]["judgment"] == "unclear"
        await answer(application, "First the paper bends, then Mia folds a new one.")
        assert core.attempts[-1]["judgment"] == "correct"
        assert len(judgments(application)) == calls + 2

    asyncio.run(scenario())


def test_the_simulated_course_runs_through_every_new_type(application):
    core, fact = asyncio.run(complete_story(application))
    judged = {e["payload"]["purpose"] for e in fact.body["events"] if e["kind"] == "rule_judged"}
    assert judged == {"choice", "order", "match", "fill"}
    purposes = [a["purpose"] for a in fact.body["attempts"]]
    assert "open" in purposes and core.end["reason"] == "normal"
    assert all(
        {"type_version", "task_started_ms", "answered_ms", "judged_ms"} <= set(a)
        for a in fact.body["attempts"]
    )
