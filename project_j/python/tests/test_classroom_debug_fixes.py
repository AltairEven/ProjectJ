"""Manual-debug regressions using simulated providers and pytest temporary stores."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from support import (
    LEARNER,
    answer,
    answer_clicked,
    confirmed,
    deliver_all,
    judgments,
    next_course,
    prepare_course,
    read_aloud_all,
    ready,
    start,
)

from project_j.application.configuration import Settings
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app


@pytest.mark.parametrize("text", ["she wants a bridge", "She wants a bridge for her car."])
def test_first_question_accepts_the_filled_sentence_frame_after_help(application, text):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "Got it.", "confirmation")
        await answer(application, "a car")
        assert core.task_index == 0 and core.attempts[0]["judgment"] == "incorrect"
        await answer(application, "Help me", "help")
        await answer(application, "I still don't understand. Help me.", "help")
        await answer(application, text)
        assert core.task_index == 1
        assert core.attempts[-1]["judgment"] == "correct"
        assert core.attempts[-1]["inputs"][0]["text"] == text
        assert core.attempts[-1]["help"]["english.reading.facts"] == "scaffold"
        core.control("stop", "stop")
        application.seal()

    asyncio.run(scenario())


def test_all_template_sentence_frames_and_saved_c2_follow_the_corrected_flow(application):
    app = application

    async def scenario():
        core = await start(app)
        deliver_all(core)
        await answer(app, "Got it.", "confirmation")
        await answer(app, "that thing")
        assert core.task_index == 0 and core.attempts[-1]["judgment"] == "unclear"
        await answer(app, "she wants a bridge")
        await answer(app, "She folded a new piece of paper.")
        assert core.task_index == 2
        await answer(app, "I pointed to the folded paper.", "report")
        core.control("returned", "return")
        deliver_all(core)
        await answer(
            app, "Same book and car; with the folded paper the car did not fall.", "report"
        )
        await read_aloud_all(app)
        await answer(app, "Got it, the summary.", "confirmation")
        assert core.mode == "ended"
        fact = app.seal()
        app.feedback("feedback", LEARNER, core.activity_id, "明确模拟：无额外补充")
        result = await next_course(app, "next", LEARNER, core.activity_id)
        course_ref = RevisionRef.model_validate(result["next_package"]["course_ref"])
        course = app.store.read(course_ref, LEARNER)
        assert course.body["demand_ref"] == app.current(LEARNER, "demand").ref.model_dump()
        await ready(app, {"course_ref": course_ref})
        assert (
            app.start("c2-start", LEARNER, course_ref, materials_ready=True)["decision"]
            == "allow_simulation"
        )
        next_core = app.core
        deliver_all(next_core)
        await answer(app, "Got it.", "confirmation")
        await answer(app, "He first tries the small bus.")
        await answer(app, "The paper bends and the big bus falls.")
        assert next_core.task_index == 2
        assert all(attempt["judgment"] == "correct" for attempt in next_core.attempts)
        next_core.control("next-stop", "stop")
        app.seal()
        assert app.store.read(fact.ref, LEARNER).body == fact.body

    asyncio.run(scenario())


def test_wrong_phase_confirmation_is_not_an_attempt_and_cannot_cancel_judgment(application):
    app = application

    async def scenario():
        core = await start(app)
        deliver_all(core)
        await answer(app, "Got it.", "confirmation")
        revision, epoch, events = core.revision, core.epoch, len(core.events)
        with pytest.raises(ValueError, match="Nothing to confirm now"):
            await app.adapter.text("unexpected", "Yes, I heard the plan.", "confirmation")
        assert not core.attempts and not judgments(app)
        assert (core.revision, core.epoch, len(core.events)) == (revision, epoch, events)
        assert "unexpected" not in core.inputs
        app.models.adapters["simulated"].delay_seconds = 0.03
        pending = asyncio.create_task(app.adapter.text("answer", "a bridge"))
        await asyncio.sleep(0.005)
        assert core.mode == "judging"
        epoch = core.epoch
        with pytest.raises(ValueError, match="Nothing to confirm now"):
            await app.adapter.text(
                "bad-supplement", "Got it.", "confirmation", "supplement", "answer"
            )
        assert core.epoch == epoch and core.mode == "judging"
        assert (await pending)["ack"] == "accepted"
        deliver_all(core)
        assert len(core.attempts) == len(judgments(app)) == 1 and core.task_index == 1
        core.control("stop", "stop")
        app.seal()

    asyncio.run(scenario())


def test_legal_confirmation_replays_and_closes_without_learning_attempts(application):
    app = application

    async def scenario():
        core = await start(app)
        deliver_all(core)
        await app.adapter.text("goal-receipt", "Yes, I heard the plan.", "confirmation")
        deliver_all(core)
        assert (await app.adapter.text("goal-receipt", "Yes, I heard the plan.", "confirmation"))[
            "ack"
        ] == "replayed"
        assert core.task_index == 0 and not core.attempts and not judgments(app)
        core.control("close", "close")
        deliver_all(core)
        await app.adapter.text("closing-receipt", "Got it, the summary.", "confirmation")
        assert core.mode == "ended" and core.end["reason"] == "normal"
        assert not core.attempts and not judgments(app)
        assert app.seal().body["attempts"] == []

    asyncio.run(scenario())


def test_api_rejects_confirmation_at_answer_wait_without_a_model_call(application):
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
        deliver_all(app.core)
        assert (
            client.post(
                "/api/class/input",
                headers=student,
                json={"event_id": "goal", "text": "收到", "intent": "confirmation"},
            ).status_code
            == 200
        )
        deliver_all(app.core)
        wrong = client.post(
            "/api/class/input",
            headers=student,
            json={"event_id": "wrong", "text": "收到", "intent": "confirmation"},
        )
        assert wrong.status_code == 400
        assert "Nothing to confirm now" in wrong.json()["message"]
        assert not app.core.attempts and not judgments(app)
        assert (
            client.post(
                "/api/class/input",
                headers=student,
                json={"event_id": "answer", "text": "she wants a bridge"},
            ).status_code
            == 200
        )
        assert app.core.attempts[-1]["judgment"] == "correct"
        client.post(
            "/api/class/control", headers=student, json={"event_id": "stop", "command": "stop"}
        )


def test_api_accepts_a_base64_recording_only_on_a_reading_segment(application):
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

        def say(event_id, text, intent="unknown"):
            reply = client.post(
                "/api/class/input",
                headers=student,
                json={"event_id": event_id, "text": text, "intent": intent},
            )
            assert reply.status_code == 200
            deliver_all(core)

        def recording(event_id, audio_base64):
            return client.post(
                "/api/class/recording",
                headers=student,
                json={
                    "event_id": event_id,
                    "audio_base64": audio_base64,
                    "input_started_ms": 0,
                    "input_ended_ms": 4100,
                    "recording_ms": 4000,
                },
            )

        def encoded(text):
            return base64.b64encode(text.encode()).decode()

        deliver_all(core)
        assert recording("too-early", encoded("a bridge")).status_code in {400, 409, 422}
        say("goal", "Got it.", "confirmation")
        say("q1", "a bridge")
        say("q2", "She folded it.")
        say("point", "I pointed to the folded paper.", "report")
        core.control("return", "return")
        deliver_all(core)
        say("offscreen", "The second time the paper was folded and the car did not fall.", "report")
        asyncio.run(answer_clicked(app))
        reference = core.reading_task().reference_text
        silent = recording("silent", "")  # Ended without reading: an unknown score, not an error.
        assert silent.status_code == 200 and silent.json()["ack"] == "accepted"
        assert core.attempts[-1]["pronunciation"]["status"] == "unknown"
        deliver_all(core)
        result = recording("read-1", encoded(reference)).json()
        assert result["ack"] == "accepted"
        assert result["snapshot"]["recording"]["reference_text"] == reference
        deliver_all(core)
        assert core.reading_task().task_id == "read-aloud-2"
        assert recording("bad", "not base64!").status_code in {400, 422}
