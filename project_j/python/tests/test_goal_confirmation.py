"""ADR-0018: a changed goal or a new capability waits for the student before preparation."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, complete_story, wait_jobs

from project_j.application.configuration import Settings
from project_j.application.progress import advance, next_step
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.learning.contracts import DemandBody, Goal, ProposedCapability
from project_j.learning.planning import (
    DemandDraft,
    PlanningUnavailable,
    apply_demand_draft,
    needs_goal_confirmation,
)
from project_j.platform.storage import Conflict

SCIENCE = {"dimension_id": "science.observation.report", "description": "Report a real observation"}
LISTENING = {
    "dimension_id": "english.listening.gist",
    "description": "Get the main idea of a short talk",
}
NEW = {
    "dimension_id": "english.listening.gist",
    "name": "Listening for the main idea",
    "definition": "Listen to a short talk and say the main idea.",
}


def planning_replies(application, monkeypatch, primary, new=(), keep_on_reject=True):
    adapter = application.models.adapters["simulated"]
    original = adapter.respond
    seen = []

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            seen.append(request.context)
            if "keep_goals" in request.context and keep_on_reject:
                return reply
            reply |= {
                "primary_goal": primary,
                "goal_reason": "Model reason: reading needed hints; observe another way.",
                "new_capabilities": list(new),
            }
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    return seen


async def acknowledged_lesson(app):
    _, fact = await complete_story(app)
    activity = fact.body["activity_id"]
    evaluation = await app.evaluate("e1", LEARNER, activity, "after_class")
    app.acknowledge("ack1", LEARNER, activity, evaluation.ref)
    advance(app, LEARNER)
    await wait_jobs(app)
    return app.current(LEARNER, "demand")


def test_unchanged_goals_go_straight_to_the_focus_choice(application):
    async def scenario():
        demand = await acknowledged_lesson(application)
        assert demand.body["goal_confirmation"] is None
        assert next_step(application, LEARNER)["kind"] == "focus"
        application.choose_focus("f1", LEARNER, demand.ref, "桥", ["past_simple"], None)
        advance(application, LEARNER)
        await wait_jobs(application)
        assert next_step(application, LEARNER)["kind"] == "course"

    asyncio.run(scenario())


def test_changed_goal_waits_for_the_student(application, monkeypatch):
    planning_replies(application, monkeypatch, SCIENCE)

    async def scenario():
        demand = await acknowledged_lesson(application)
        assert demand.body["goal_confirmation"] == "needed"
        step = next_step(application, LEARNER)
        assert step["kind"] == "goal"
        assert step["subject"]["demand_ref"] == demand.ref.model_dump()
        assert step["subject"]["goal_reason"].startswith("Model reason")
        assert advance(application, LEARNER) is None
        with pytest.raises(Conflict, match="Confirm the new goal first"):
            await application.prepare("p-early", LEARNER)

    asyncio.run(scenario())


def test_accepting_creates_the_new_capability_then_prepares(application, monkeypatch):
    planning_replies(application, monkeypatch, LISTENING, new=(NEW,))
    demand = asyncio.run(acknowledged_lesson(application))
    assert demand.body["proposed_capabilities"] == [NEW]
    body = {
        "request_id": "goal1",
        "learner_id": LEARNER,
        "demand_ref": demand.ref.model_dump(),
        "choice": "accept",
    }
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}
        assert client.post("/api/goal/decide", headers=auth, json=body).status_code == 200
    catalog = {
        doc.body["dimension_id"] for doc in application.store.list_current(LEARNER, "capability")
    }
    assert NEW["dimension_id"] in catalog
    confirmed = application.current(LEARNER, "demand")
    assert confirmed.body["goal_confirmation"] == "confirmed"
    assert confirmed.source_refs == demand.source_refs  # Still planned from that evaluation.

    assert next_step(application, LEARNER)["kind"] == "focus"
    application.choose_focus("f1", LEARNER, confirmed.ref, "桥", [], None)

    async def scenario():
        assert advance(application, LEARNER).startswith("prepare:")
        application.jobs.clear()  # The simulated model has no template for listening.

    asyncio.run(scenario())


def test_rejecting_replans_with_the_old_goals(application, monkeypatch):
    seen = planning_replies(application, monkeypatch, SCIENCE)
    before = application.current(LEARNER, "demand").body
    demand = asyncio.run(acknowledged_lesson(application))
    asyncio.run(application.decide_goal("goal1", LEARNER, demand.ref, "reject"))
    kept = application.current(LEARNER, "demand").body
    assert kept["primary_goal"] == before["primary_goal"]
    assert kept["goal_confirmation"] == "confirmed"
    assert seen[-1]["keep_goals"] == {
        "primary_goal": before["primary_goal"],
        "auxiliary_goal": before["auxiliary_goal"],
    }


def test_rejected_replan_that_changes_goals_again_is_refused(application, monkeypatch):
    planning_replies(application, monkeypatch, SCIENCE, keep_on_reject=False)
    demand = asyncio.run(acknowledged_lesson(application))
    with pytest.raises(PlanningUnavailable, match="the goals were not kept"):
        asyncio.run(application.decide_goal("goal1", LEARNER, demand.ref, "reject"))
    assert application.current(LEARNER, "demand").ref == demand.ref


def test_new_capability_must_be_new_and_english(application):
    current = DemandBody.model_validate(application.current(LEARNER, "demand").body)
    base = {
        "suggestion_action": "adopt",
        "suggestion_reason": "Reason",
        "direction": "observe",
        "auxiliary_goal": current.auxiliary_goal.model_dump(),
        "prerequisites": [],
        "sampling_requirements": ["First answers"],
        "help_requirements": ["Change the way"],
        "offscreen_requirements": "None",
        "fallback": "None",
        "gaps": [],
        "plan_effect": "reused",
        "plan_reason": "Kept",
        "plan_patch": None,
        "goal_reason": "Reason",
        "note": {"before": "a", "after": "b", "why": "c", "uncertain": [], "expectation": "d"},
    }
    known = {"english.reading.facts", "english.speaking.report"}
    ref = RevisionRef(object_id="x", revision=1)
    for proposed in (
        {"dimension_id": "english.reading.facts", "name": "Repeat", "definition": "Already there"},
        {"dimension_id": "math.counting", "name": "Counting", "definition": "Not English"},
    ):
        draft = DemandDraft.model_validate(
            base
            | {
                "primary_goal": {"dimension_id": proposed["dimension_id"], "description": "d"},
                "new_capabilities": [proposed],
            }
        )
        with pytest.raises(PlanningUnavailable, match="a new skill entry is not valid"):
            apply_demand_draft(draft, current, known, ref, ref, ref, plan_adjusted=False)
    placement = current.model_copy(update={"purpose": "placement"})
    same = current.model_copy(update={"goal_confirmation": None})
    assert needs_goal_confirmation(placement, same)
    assert not needs_goal_confirmation(current, same)
    assert ProposedCapability.model_validate(NEW).name == "Listening for the main idea"
    assert Goal.model_validate(LISTENING).dimension_id == "english.listening.gist"


def test_goal_decision_logs_choice_only(application, monkeypatch, caplog):
    planning_replies(application, monkeypatch, LISTENING, new=(NEW,))
    demand = asyncio.run(acknowledged_lesson(application))
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.decide_goal("goal1", LEARNER, demand.ref, "accept"))
    assert "goal decided choice=accept new=1" in caplog.text
    assert "Listening for the main idea" not in caplog.text
