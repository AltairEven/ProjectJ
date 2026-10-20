import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from support import CONDITIONS, LEARNER, answer, deliver_all, ready, start
from test_preparation_model import ref, science_draft, science_lesson

from project_j.application.configuration import Settings
from project_j.classroom.harness import DeliveryFeedback
from project_j.evaluation.assessment import evaluation_request
from project_j.interface.api import create_app
from project_j.platform.models.openai_api import solid_png
from project_j.platform.storage import Conflict
from project_j.preparation.resources import adopt_resource, generate_resource


async def to_reading(app):
    core = await start(app)
    deliver_all(core)
    await answer(app, "听到了。", "confirmation")
    await answer(app, "a bridge")
    await answer(app, "She folded it.")
    await answer(app, "我指的是折过的纸。", "report")
    core.control(str(uuid4()), "return")
    deliver_all(core)
    await answer(app, "第二次纸折了，车没有掉。", "report")
    assert core.reading_task() is not None
    return core


def test_start_needs_adopted_required_resources(application, caplog):
    course_ref = ref(asyncio.run(application.prepare("prepare1", LEARNER))["course_ref"])
    application.confirm("c1", LEARNER, course_ref)
    caplog.set_level(logging.INFO, logger="project_j")
    with pytest.raises(Conflict, match="资源未就绪：朗读示范"):
        application.start("s1", LEARNER, course_ref, CONDITIONS)
    assert "start refused reason=resources_missing" in caplog.text
    asyncio.run(ready(application, {"course_ref": course_ref}))
    assert application.start("s2", LEARNER, course_ref, CONDITIONS)["decision"] == (
        "allow_simulation"
    )
    assert "start resources recorded=" in caplog.text


def test_changed_voice_counts_as_missing(application, monkeypatch):
    course_ref = ref(asyncio.run(application.prepare("prepare1", LEARNER))["course_ref"])
    asyncio.run(ready(application, {"course_ref": course_ref}))
    monkeypatch.setattr(application.synthesizer, "voice", "another-voice")
    with pytest.raises(Conflict, match="资源未就绪"):
        application.start("s1", LEARNER, course_ref, CONDITIONS)


def test_reading_question_does_not_read_the_passage(application):
    core = asyncio.run(to_reading(application))
    task, action = core.reading_task(), core.actions[core.pending or core.presented]
    assert action.speech == (task.prompt,) and task.reference_text in action.text


def test_second_help_plays_the_adopted_demonstration(application):
    async def scenario():
        core = await to_reading(application)
        task = core.reading_task()
        await answer(application, "帮我一下", "help")
        first = core.actions[core.presented]
        await answer(application, "还是不会，帮我一下", "help")
        return task, first, core.actions[core.presented]

    task, first, second = asyncio.run(scenario())
    assert first.kind == "help" and first.speech == ()
    assert second.help == {"english.speaking.pronunciation": "full_model"}
    assert second.speech == (second.text, task.reference_text)
    assert task.reference_text in application.recorded


def test_no_demonstration_without_adopted_audio(application, monkeypatch):
    async def scenario():
        core = await to_reading(application)
        core.voiced = frozenset()  # As if the demonstration had not been adopted.
        await answer(application, "帮我一下", "help")
        await answer(application, "还是不会，帮我一下", "help")
        return core.actions[core.presented]

    assert asyncio.run(scenario()).speech == ()


def test_repeat_keeps_speech(application):
    async def scenario():
        core = await to_reading(application)
        core.control(str(uuid4()), "repeat")
        return core.reading_task(), core.actions[core.pending]

    task, repeated = asyncio.run(scenario())
    assert repeated.kind == "repeat" and repeated.speech == (task.prompt,)


def visual_draft(context):
    draft = science_draft(context)
    picture = draft["resources"][0] | {"required": True, "task_ids": ["cup-fact"]}
    draft["resources"] = [
        picture,
        picture
        | {"resource_id": "cup-motion", "kind": "animation", "required": False, "stage": "help"},
        picture | {"resource_id": "cup-note", "required": False, "audience": "parent"},
    ]
    return draft


def visual_class(application, monkeypatch, client=None):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    asyncio.run(ready(application, {"course_ref": course_ref}))
    for name in ("cup-motion", "cup-note"):
        made = asyncio.run(generate_resource(application, f"g:{name}", LEARNER, course_ref, name))
        adopt_resource(application, f"a:{name}", LEARNER, course_ref, name, made["version"])
    result = application.start("visual", LEARNER, course_ref, CONDITIONS)
    core = application.core
    deliver_all(core)
    asyncio.run(answer(application, "听到了。", "confirmation"))
    return result, core


def test_visuals_follow_their_stage(application, monkeypatch):
    _, core = visual_class(application, monkeypatch)
    question = core.actions[core.presented]
    assert question.kind == "question" and question.visuals == ("cup-picture",)
    asyncio.run(answer(application, "帮我一下", "help"))
    helped = core.actions[core.presented]
    assert helped.kind == "help" and helped.visuals == ("cup-motion",)
    assert all("cup-note" not in action.visuals for action in core.actions.values())
    assert set(application.visuals) == {"cup-picture", "cup-motion"}


def test_student_speech_and_visual_endpoints(application, monkeypatch):
    with TestClient(create_app(Settings(), application)) as client:
        result, core = visual_class(application, monkeypatch)
        student = {"Authorization": f"Bearer {result['student_token']}"}
        question = core.actions[core.presented]
        url = f"/api/student/visual/{question.action_id}"
        picture = client.get(f"{url}/cup-picture", headers=student)
        assert picture.status_code == 200 and picture.content == solid_png()
        assert picture.headers["content-type"] == "image/png"
        assert picture.headers["content-security-policy"] == "sandbox"
        assert client.get(f"{url}/cup-motion", headers=student).status_code == 404
        assert client.get(f"{url}/cup-picture").status_code == 403
        core.control(str(uuid4()), "stop")


def test_speech_plays_the_recorded_demonstration(application):
    with TestClient(create_app(Settings(), application)) as client:

        async def scenario():
            core = await to_reading(application)
            await answer(application, "帮我一下", "help")
            await answer(application, "还是不会，帮我一下", "help")
            return core

        core = asyncio.run(scenario())
        token = application.start_requests["start1"]["result"]["student_token"]
        demo = core.actions[core.presented]
        reply = client.get(
            f"/api/student/speech/{demo.action_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert reply.status_code == 200
        assert reply.content.endswith(application.recorded[core.reading_task().reference_text])
        assert reply.headers["x-speech-source"] == "live"  # Jane's own words come first, live.
        core.control(str(uuid4()), "stop")


def test_a_fully_recorded_sentence_says_so(application):
    with TestClient(create_app(Settings(), application)) as client:
        core = asyncio.run(to_reading(application))
        token = application.start_requests["start1"]["result"]["student_token"]
        question = core.actions[core.pending or core.presented]
        application.recorded[question.speech[0]] = b"\x01\x00" * 8  # As if adopted.
        reply = client.get(
            f"/api/student/speech/{question.action_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        # The whole recording is there at once; the page plays it as one file.
        assert reply.headers["x-speech-source"] == "recorded"
        assert reply.content == application.recorded[question.speech[0]]
        core.control(str(uuid4()), "stop")


def shown(core, action, resources, status="completed"):
    return core.delivery(
        DeliveryFeedback(
            delivery_event_id=str(uuid4()),
            delivery_id=f"{action.action_id}:visual",
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


def test_picture_report_is_a_fact_that_moves_nothing(application, monkeypatch):
    _, core = visual_class(application, monkeypatch)
    question = core.actions[core.presented]
    before = (core.mode, core.wait_kind, core.pending, core.revision, dict(core.help_by_task))
    shown(core, question, ("cup-picture",))
    assert (core.mode, core.wait_kind, core.pending, core.revision) == before[:4]
    assert core.help_by_task == before[4]
    event = core.events[-1]
    assert event["kind"] == "delivery" and event["payload"]["modality"] == "image"
    assert event["payload"]["delivered_range"] == ["cup-picture"]


def test_picture_report_names_only_the_actions_own_visuals(application, monkeypatch):
    _, core = visual_class(application, monkeypatch)
    with pytest.raises(ValueError, match="visual"):
        shown(core, core.actions[core.presented], ("cup-motion",))


def test_an_attempt_keeps_the_pictures_seen_before_it(application, monkeypatch):
    _, core = visual_class(application, monkeypatch)
    shown(core, core.actions[core.presented], ("cup-picture",))
    asyncio.run(answer(application, "帮我一下", "help"))
    help_action = core.actions[core.presented]
    shown(core, help_action, (), status="failed")  # The animation never displayed.
    asyncio.run(answer(application, "the cup"))
    attempt = core.attempts[-1]
    assert attempt["visuals"] == [{"resource_id": "cup-picture", "stage": "question"}]
    fact = {"end": {}, "attempts": core.attempts, "limitations": []}
    context = evaluation_request(fact, core.course.model_dump(mode="json"), [], None).context
    assert context["attempts"][-1]["visuals"] == attempt["visuals"]
