"""Stories at the student's Lexile plus a chosen step; the student's rating moves the step
(ADR-0023)."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from support import complete_story, prepare_course, wait_jobs

from project_j.application.configuration import Settings
from project_j.application.documents import identity
from project_j.application.evaluating import story_rateable
from project_j.application.progress import advance, next_step
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.learning.contracts import challenge_of, rated
from project_j.learning.onboarding import ProfileEdit, confirm_profile
from project_j.preparation.difficulty import (
    lexile_number,
    lexile_text,
    story_level,
    story_words,
    targets,
)


def edit(app, **changes) -> ProfileEdit:
    """The profile form as saved, with some fields changed."""
    body = app.current(app.learner_id, "profile").body
    fields = ("background", "interests", "weekly_time", "focus", "materials")
    return ProfileEdit(**{key: body[key] for key in fields} | changes)


def test_lexile_numbers_round_trip():
    assert lexile_number("650L") == 650 and lexile_number("BR100L") == -100
    assert (lexile_text(-100), lexile_text(0), lexile_text(750)) == ("BR100L", "0L", "750L")


@pytest.mark.parametrize(
    "target,band",
    [
        (-100, (50, 150)),
        (199, (50, 150)),
        (200, (100, 250)),
        (450, (150, 350)),
        (650, (250, 450)),
        (999, (350, 550)),
        (1000, (400, 600)),
        (1400, (400, 600)),
    ],
)
def test_story_words_follow_the_target(target, band):
    assert story_words(target) == band


def test_story_level_adds_the_challenge():
    level = story_level({"lexile": "550L", "challenge": "little"}, placement=False)
    assert (level.lexile, level.challenge, level.target, level.words) == (
        "550L",
        "little",
        "600L",
        (250, 450),
    )
    assert story_level({"lexile": "550L"}, False).target == "650L"
    assert story_level({"lexile": "BR150L", "challenge": "harder"}, False).target == "0L"
    assert story_level({"lexile": "BR300L", "challenge": "same"}, False).target == "BR300L"
    placed = story_level({"lexile": "550L", "challenge": "harder"}, placement=True)
    assert (placed.challenge, placed.target) == (None, "550L")
    assert story_level({"lexile": None}, False) is None


def test_targets_for_the_focus_step():
    assert targets("550L") == {
        "same": "550L",
        "little": "600L",
        "challenge": "650L",
        "harder": "700L",
    }
    assert targets(None) == {}


@pytest.mark.parametrize(
    "challenge,rating,after",
    [
        ("challenge", "too_easy", "harder"),
        ("challenge", "too_hard", "little"),
        ("challenge", "just_right", "challenge"),
        ("harder", "too_easy", "harder"),
        ("same", "too_hard", "same"),
    ],
)
def test_a_rating_moves_one_step_within_the_ends(challenge, rating, after):
    assert rated(challenge, rating) == after


def test_the_profile_form_saves_the_challenge(application):
    learner = application.learner_id
    assert challenge_of(application.current(learner, "profile").body) == "challenge"
    confirm_profile(application, "p1", learner, edit(application, challenge="harder"))
    confirm_profile(application, "p2", learner, edit(application))
    assert application.current(learner, "profile").body["challenge"] == "harder"


def test_a_prepared_course_records_its_story_level(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j.application.preparing")
    prepared = prepare_course(application)
    course_ref = RevisionRef.model_validate(prepared["course_ref"])
    course = application.store.read(course_ref, application.learner_id)
    assert course.body["story_level"] == {
        "lexile": "500L",
        "challenge": "challenge",
        "target": "600L",
        "words": [250, 450],
    }
    assert "story level challenge=challenge target=600L words=250-450" in caplog.text


async def at_focus(app):
    """After a lesson: evaluated, acknowledged, adopted and planned; the topic waits."""
    _, fact = await complete_story(app)
    activity = fact.body["activity_id"]
    evaluation = await app.evaluate("e1", app.learner_id, activity, "after_class")
    app.acknowledge("a1", app.learner_id, activity, evaluation.ref)
    await app.adopt("ad1", app.learner_id, evaluation)
    demand = app.current(app.learner_id, "demand")
    if demand.body.get("goal_confirmation") == "needed":
        await app.decide_goal("g1", app.learner_id, demand.ref, "accept")
    return app.current(app.learner_id, "demand")


def test_the_focus_step_offers_each_target(application):
    asyncio.run(at_focus(application))
    step = next_step(application, application.learner_id)
    assert step["kind"] == "focus"
    assert step["subject"]["challenge"] == "challenge"
    assert step["subject"]["targets"] == {
        "same": "500L",
        "little": "550L",
        "challenge": "600L",
        "harder": "650L",
    }


def test_choosing_a_harder_story_with_the_topic_saves_it_once(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j.application.adopting")
    demand, learner = asyncio.run(at_focus(application)), application.learner_id
    before = application.current(learner, "profile").revision
    for _ in range(2):
        application.choose_focus("f1", learner, demand.ref, "Boats", [], None, "harder")
    profile = application.current(learner, "profile")
    assert (profile.body["challenge"], profile.revision) == ("harder", before + 1)
    assert application.current(learner, "demand").body["topic"] == "Boats"
    assert "challenge=harder changed=true" in caplog.text


def test_keeping_the_challenge_adds_no_profile_revision(application):
    demand, learner = asyncio.run(at_focus(application)), application.learner_id
    before = application.current(learner, "profile").revision
    application.choose_focus("f1", learner, demand.ref, "Boats", [], None, "challenge")
    assert application.current(learner, "profile").revision == before


async def evaluated(app):
    _, fact = await complete_story(app)
    activity = fact.body["activity_id"]
    return activity, await app.evaluate("e1", app.learner_id, activity, "after_class")


def test_too_easy_raises_the_next_lessons(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j.application.evaluating")
    activity, evaluation = asyncio.run(evaluated(application))
    learner = application.learner_id
    application.acknowledge("a1", learner, activity, evaluation.ref, "too_easy")
    assert application.current(learner, "profile").body["challenge"] == "harder"
    mark = application.store.current(identity(activity, "acknowledgement"), learner)
    assert mark.body["story_rating"] == "too_easy"
    assert "rating=too_easy challenge=challenge->harder" in caplog.text


def test_a_replayed_acknowledge_moves_the_setting_once(application):
    activity, evaluation = asyncio.run(evaluated(application))
    learner = application.learner_id
    before = application.current(learner, "profile").revision
    for _ in range(2):
        application.acknowledge("a1", learner, activity, evaluation.ref, "too_hard")
    profile = application.current(learner, "profile")
    assert (profile.body["challenge"], profile.revision) == ("little", before + 1)


def test_a_rating_at_the_end_keeps_the_setting(application):
    learner = application.learner_id
    confirm_profile(application, "p1", learner, edit(application, challenge="harder"))
    activity, evaluation = asyncio.run(evaluated(application))
    before = application.current(learner, "profile").revision
    application.acknowledge("a1", learner, activity, evaluation.ref, "too_easy")
    assert application.current(learner, "profile").revision == before


async def jobs_done(app):
    """What the service does after page loads: start the next AI job, wait, until none starts."""
    while advance(app, app.learner_id):
        await wait_jobs(app)


def test_a_second_acknowledgement_cannot_be_rated(application):
    activity, evaluation = asyncio.run(evaluated(application))
    learner = application.learner_id
    application.acknowledge("a1", learner, activity, evaluation.ref, "too_easy")
    application.feedback("objection1", learner, activity, "x", ({"note": "x"},))
    asyncio.run(jobs_done(application))
    corrected = application.store.current(identity(activity, "evaluation"), learner)
    assert corrected.ref != evaluation.ref
    with pytest.raises(ValueError, match="cannot be rated"):
        application.acknowledge("a2", learner, activity, corrected.ref, "too_easy")
    application.acknowledge("a3", learner, activity, corrected.ref)  # Without a rating it works.
    assert application.current(learner, "profile").body["challenge"] == "harder"


FIRST_READ = [{"kind": "reading_finished", "payload": {"first_reading": True}}]


@pytest.mark.parametrize(
    "course,events,expected",
    [
        ({"story_level": {"challenge": "challenge"}}, FIRST_READ, True),
        (
            {"story_level": {"challenge": "challenge"}},
            [{"kind": "reading_unfinished", "payload": {"first_reading": True}}],
            True,
        ),
        ({"story_level": None}, FIRST_READ, False),
        ({}, FIRST_READ, False),
        ({"story_level": {"challenge": None}}, FIRST_READ, False),
        (
            {"story_level": {"challenge": "challenge"}},
            [{"kind": "reading_finished", "payload": {"first_reading": False}}],
            False,
        ),
        ({"story_level": {"challenge": "challenge"}}, [], False),
    ],
)
def test_story_rateable_needs_a_level_and_a_first_reading(course, events, expected):
    assert story_rateable(course, events) is expected


def test_the_page_rates_through_the_acknowledge_route(application):
    activity, evaluation = asyncio.run(evaluated(application))
    learner = application.learner_id
    with TestClient(create_app(Settings(), application)) as client:
        reply = client.post(
            "/api/evaluation/acknowledge",
            headers={"Authorization": f"Bearer {application.token}"},
            json={
                "request_id": "ack-http",
                "learner_id": learner,
                "activity_id": activity,
                "evaluation_ref": evaluation.ref.model_dump(),
                "rating": "just_right",
            },
        )
    assert reply.status_code == 200
    assert application.current(learner, "profile").body.get("challenge") is None
    mark = application.store.current(identity(activity, "acknowledgement"), learner)
    assert mark.body["story_rating"] == "just_right"
