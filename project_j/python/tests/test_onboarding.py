"""ADR-0018 onboarding: Lexile, a short chat with Jane, a placement lesson, then the profile."""

import asyncio
import logging
import time

import pytest
from fastapi.testclient import TestClient
from support import (
    LEARNER,
    answer,
    deliver_all,
    onboarding_app,
    read_aloud_all,
    read_story,
    ready,
    wait_jobs,
)

from project_j.application.configuration import Settings
from project_j.application.progress import advance, next_step
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.learning.fixtures import initialize
from project_j.learning.onboarding import (
    QUESTIONS,
    ProfileEdit,
    confirm_profile,
    save_chat,
    save_lexile,
)
from project_j.preparation.lessons import lesson_of

ANSWERS = ["为了工作和旅行", "喜欢做饭和科幻", "每周三小时", "口语和听力", "No extra help."][
    : len(QUESTIONS)
]
EDIT = ProfileEdit(
    background="成人，工作中偶尔读英文邮件。",
    interests="做饭、科幻",
    weekly_time="每周三小时",
    focus="口语和听力",
)


async def placement_lesson(app):
    """Start the prepared placement lesson, answer, read and stop; then let it evaluate."""
    step = next_step(app, LEARNER)
    assert step["kind"] == "placement"
    course_ref = RevisionRef.model_validate(step["subject"]["course_ref"])
    await ready(app, {"course_ref": course_ref})
    folder = lesson_of(app, LEARNER, course_ref).body["folder"]
    assert folder.startswith("lessons/placement-001-")  # Its own numbering (ADR-0027).
    assert app.start("placement", LEARNER, course_ref)["decision"] == "allow_simulation"
    core = app.core
    deliver_all(core)
    read_story(app.core)
    await answer(app, "a bridge")
    await answer(app, "She folded it.")
    await read_aloud_all(app)
    await answer(app, "Yes, I heard.", "confirmation")
    assert core.mode == "ended"
    app.seal()
    advance(app, LEARNER)
    await wait_jobs(app)


def test_onboarding_walks_from_lexile_to_the_first_goal(tmp_path):
    app = onboarding_app(tmp_path)

    async def scenario():
        initialize(app)
        assert next_step(app, LEARNER)["kind"] == "lexile"
        save_lexile(app, "l1", LEARNER, " 650l ")
        assert app.current(LEARNER, "profile").body["lexile"] == "650L"
        assert next_step(app, LEARNER)["kind"] == "chat"
        save_chat(app, "c1", LEARNER, ANSWERS)
        demand = app.current(LEARNER, "demand")
        assert demand.body["purpose"] == "placement"
        assert demand.body["auxiliary_goal"]["dimension_id"] == "english.speaking.pronunciation"
        advance(app, LEARNER)
        await wait_jobs(app)  # the placement lesson is prepared
        await placement_lesson(app)  # taught, evaluated, profile summarized
        step = next_step(app, LEARNER)
        assert step["kind"] == "profile"
        profile = app.current(LEARNER, "profile").body
        assert profile["summarized"] and profile["background"]
        confirm_profile(app, "p1", LEARNER, EDIT)
        assert app.current(LEARNER, "profile").body["confirmed"] is True
        advance(app, LEARNER)
        await wait_jobs(app)  # the placement evaluation is adopted and planned from
        step = next_step(app, LEARNER)
        assert step["kind"] == "goal"
        assert app.current(LEARNER, "demand").body["purpose"] == "lesson"
        await app.decide_goal("g1", LEARNER, app.current(LEARNER, "demand").ref, "accept")
        assert next_step(app, LEARNER)["kind"] == "focus"  # the placement itself never asked
        app.choose_focus("f1", LEARNER, app.current(LEARNER, "demand").ref, "桥", [], None)
        advance(app, LEARNER)
        await wait_jobs(app)  # the first lesson is prepared, in a simulation from the template
        assert not app.job_failures
        assert next_step(app, LEARNER)["kind"] == "course"

    asyncio.run(scenario())


def test_chat_needs_every_answer(tmp_path):
    app = onboarding_app(tmp_path)
    initialize(app)
    with pytest.raises(LookupError):
        save_chat(app, "c0", LEARNER, ANSWERS)  # no Lexile yet
    save_lexile(app, "l1", LEARNER, "650L")
    with pytest.raises(ValueError):
        save_chat(app, "c1", LEARNER, ANSWERS[:-1])
    with pytest.raises(ValueError):
        save_chat(app, "c2", LEARNER, ANSWERS[:-1] + ["  "])
    with pytest.raises(ValueError):
        save_lexile(app, "l2", LEARNER, "650")


def test_profile_change_after_onboarding_keeps_history(application):
    before = application.current(LEARNER, "profile")
    edit = EDIT.model_copy(update={"lexile": "700l", "materials": "纸、书和小车"})
    confirm_profile(application, "edit1", LEARNER, edit)
    after = application.current(LEARNER, "profile")
    assert after.revision == before.revision + 1 and after.body["lexile"] == "700L"
    assert application.store.read(before.ref, LEARNER).body["lexile"] == "500L"
    assert after.body["chat"] == before.body["chat"]
    # Planning and preparation read the latest profile; an edit does not void the demand.
    prepared = asyncio.run(application.prepare("p-after-edit", LEARNER))
    assert prepared["course_ref"]


def test_onboarding_routes(tmp_path):
    app = onboarding_app(tmp_path)
    with TestClient(create_app(Settings(), app)) as client:
        auth = {"Authorization": f"Bearer {app.token}"}
        assert client.get("/api/dashboard", headers=auth).json()["next"]["kind"] == "lexile"
        assert client.get("/api/onboarding/questions", headers=auth).status_code == 404
        speech = client.get("/api/onboarding/speech/0", headers=auth)
        assert speech.status_code == 200 and speech.content
        assert client.get("/api/onboarding/speech/99", headers=auth).status_code == 404
        bad = {"request_id": "l0", "learner_id": LEARNER, "lexile": "650"}
        assert client.post("/api/onboarding/lexile", headers=auth, json=bad).status_code == 400
        good = bad | {"request_id": "l1", "lexile": "650L"}
        assert client.post("/api/onboarding/lexile", headers=auth, json=good).status_code == 200
        chat = {"request_id": "c1", "learner_id": LEARNER, "answers": ANSWERS}
        assert client.post("/api/onboarding/chat", headers=auth, json=chat).status_code == 200
        edit = {"request_id": "p1", "learner_id": LEARNER} | EDIT.model_dump()
        assert client.post("/api/profile", headers=auth, json=edit).status_code == 200


def test_onboarding_logs_never_carry_answers_or_lexile(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    app = onboarding_app(tmp_path)
    initialize(app)
    save_lexile(app, "l1", LEARNER, "650L")
    save_chat(app, "c1", LEARNER, ANSWERS)
    assert "onboarding lexile saved" in caplog.text
    assert f"onboarding chat saved answers={len(QUESTIONS)}" in caplog.text
    for text in ("650L", *ANSWERS):
        assert text not in caplog.text


def test_simulation_default_skips_onboarding(application):
    assert next_step(application, LEARNER)["kind"] not in {"lexile", "chat", "placement"}


def test_the_chat_questions_are_said_ahead_while_onboarding_waits(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    app = onboarding_app(tmp_path)
    with TestClient(create_app(Settings(), app)) as client:
        for _ in range(200):
            if "onboarding speech pregenerated" in caplog.text:
                break
            time.sleep(0.05)
        auth = {"Authorization": f"Bearer {app.token}"}
        speech = client.get("/api/onboarding/speech/0", headers=auth)
        assert speech.headers["x-speech-source"] == "recorded"  # whole, no waiting on synthesis
    assert f"onboarding speech pregenerated count={len(QUESTIONS)} failed=0" in caplog.text
    for question in QUESTIONS:
        assert question not in caplog.text


def test_no_chat_questions_are_said_ahead_after_onboarding(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), application)):
        pass
    assert "onboarding speech pregenerated" not in caplog.text


def test_chat_questions_are_english_only():
    from project_j.english import chinese

    assert not chinese(QUESTIONS)


def test_a_profile_summary_with_chinese_fails(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="project_j")
    app = onboarding_app(tmp_path)
    adapter = app.models.adapters["simulated"]
    original = adapter.respond

    def chinese_profile(request):
        reply = original(request)
        if request.purpose == "planning" and "chat" in request.context:
            return reply | {"background": "为了工作。"}
        return reply

    monkeypatch.setattr(adapter, "respond", chinese_profile)

    async def scenario():
        initialize(app)
        save_lexile(app, "l1", LEARNER, "650L")
        save_chat(app, "c1", LEARNER, ANSWERS)
        advance(app, LEARNER)
        await wait_jobs(app)
        await placement_lesson(app)

    asyncio.run(scenario())
    assert any("the reply was not in English" in text for text in app.job_failures.values())
    assert "profile summary failure=language" in caplog.text and "为了工作" not in caplog.text
