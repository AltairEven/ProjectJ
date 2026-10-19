import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, complete_story

from project_j.application.configuration import Settings
from project_j.application.documents import identity
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.learning.contracts import DemandBody
from project_j.learning.maintenance import review
from project_j.learning.planning import (
    DemandDraft,
    PlanningUnavailable,
    ReviewDraft,
    demand_request,
    review_request,
)
from project_j.platform.models.simulated import SimulatedAdapter
from project_j.preparation.packages import template_gap

LEVELS = ("long_term", "annual", "stage", "week")


def taught(application, feedback="孩子说很好玩。"):
    """One sealed lesson with feedback and a saved evaluation; returns the activity id."""
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, feedback, [])
    asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))
    return activity


def bodies(application, activity):
    demand = application.current(LEARNER, "demand")
    return {
        "evaluation": application.store.current(identity(activity, "evaluation"), LEARNER).body,
        "state": application.current(LEARNER, "state").body,
        "plans": {level: application.current(LEARNER, f"plan:{level}").body for level in LEVELS},
        "household": application.store.read(
            RevisionRef.model_validate(demand.body["household_ref"]), LEARNER
        ).body,
        "catalog": [doc.body for doc in application.store.list_current(LEARNER, "capability")],
        "suggestion": application.store.current(identity(activity, "suggestion"), LEARNER).body,
        "demand": demand.body,
    }


def requests(application, activity):
    data = bodies(application, activity)
    demand = demand_request(**data)
    review = review_request(
        "week",
        data["plans"],
        data["state"],
        [data["evaluation"]],
        data["household"],
        data["catalog"],
    )
    return data, demand, review


def test_demand_request_carries_learning_data(application):
    data, request, _ = requests(application, taught(application))
    context = request.context
    assert request.purpose == "planning" and request.output is DemandDraft
    assert request.deadline_seconds == 180
    assert {item["dimension_id"] for item in context["catalog"]} == {
        "english.reading.facts",
        "english.speaking.report",
        "english.speaking.pronunciation",
        "science.observation.report",
    }
    assert context["suggestion"]["direction"] == data["suggestion"]["direction"]
    assert context["demand"]["primary_goal"]["dimension_id"] == "english.reading.facts"
    assert set(context["plans"]) == set(LEVELS)
    assert context["evaluation"]["report"] == data["evaluation"]["report"]


def test_requests_carry_no_identity(application):
    _, demand, review = requests(application, taught(application))
    assert review.purpose == "planning" and review.output is ReviewDraft
    # The real model once read Jane as the learner; the role is stated in both instructions.
    assert all("Jane是唯一的教师，不是学生" in r.instructions for r in (demand, review))
    for request in (demand, review):
        text = json.dumps(request.context, ensure_ascii=False)
        for word in (LEARNER, "learner_id", "provider", "object_id", "_ref"):
            assert word not in text


def test_simulated_planning_keeps_goals_and_adopts(application):
    data, demand, review = requests(application, taught(application))
    adapter = SimulatedAdapter()
    draft = DemandDraft.model_validate(adapter.respond(demand))
    assert draft.primary_goal.model_dump() == data["demand"]["primary_goal"]
    assert draft.auxiliary_goal.model_dump() == data["demand"]["auxiliary_goal"]
    assert draft.suggestion_action == "adopt" and draft.plan_effect == "reused"
    assert ReviewDraft.model_validate(adapter.respond(review)).plan_effect == "reused"


SCIENCE = {"dimension_id": "science.observation.report", "description": "说出纸桥的实际条件与结果"}
REPORT = {"dimension_id": "english.speaking.report", "description": "用短英语报告结果"}


def model_changes_goals(application, monkeypatch, primary=SCIENCE, adjust=True):
    """The planning model moves the next lesson to science observation."""
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            reply |= {
                "suggestion_action": "modify",
                "suggestion_reason": "模型理由：阅读两题都需要提示，下节先观察实际报告。",
                "direction": "observe",
                "primary_goal": primary,
                "auxiliary_goal": REPORT,
                "plan_effect": "adjusted" if adjust else "reused",
                "plan_reason": "本周先补实际观察报告的样本。",
                "plan_patch": {"main_goal": primary} if adjust else None,
            }
        return reply

    monkeypatch.setattr(adapter, "respond", respond)


def evaluation_of(application, activity):
    return application.store.current(identity(activity, "evaluation"), LEARNER)


def test_model_demand_changes_goals_and_explains(application, monkeypatch, caplog):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    demand = application.current(LEARNER, "demand")
    week = application.current(LEARNER, "plan:week")
    assert demand.body["primary_goal"] == SCIENCE and demand.body["direction"] == "observe"
    assert demand.body["note"]["after"]
    choice = demand.body["suggestion_choices"][0]
    assert choice["action"] == "modify" and choice["reason"].startswith("模型理由")
    assert week.body["main_goal"] == SCIENCE and week.revision == 2
    assert demand.body["plan_ref"] == week.ref.model_dump()
    assert (
        result["plan_effect"] == "adjusted"
        and result["plan_reason"] == "本周先补实际观察报告的样本。"
    )
    assert f"planning activity={activity}" in caplog.text and "孩子说很好玩" not in caplog.text


def test_unknown_goal_rejects_the_draft_and_retry_succeeds(application, monkeypatch):
    activity = taught(application)
    demand, week = application.current(LEARNER, "demand"), application.current(LEARNER, "plan:week")
    model_changes_goals(
        application, monkeypatch, {"dimension_id": "math.counting", "description": "数数"}
    )
    with pytest.raises(PlanningUnavailable, match="目标不在能力目录"):
        asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert application.current(LEARNER, "demand").ref == demand.ref
    assert application.current(LEARNER, "plan:week").ref == week.ref
    assert application.current(LEARNER, "state").revision == 2  # Adopted before the model call.
    monkeypatch.undo()
    result = asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert result["demand_ref"]["revision"] == demand.revision + 1
    assert application.current(LEARNER, "state").revision == 2


def test_planning_failure_keeps_state_and_retries(application):
    activity = taught(application)
    body = {"request_id": "next1", "learner_id": LEARNER, "activity_id": activity}
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        application.models.adapters["simulated"].fail_next = True
        failed = client.post("/api/parent/next-course", headers=parent, json=body)
        assert failed.status_code == 503
        assert failed.json()["message"].startswith("下节需求未取得（模型暂不可用）")
        state = application.current(LEARNER, "state")
        assert client.post("/api/parent/next-course", headers=parent, json=body).status_code == 200
    assert application.current(LEARNER, "state").ref == state.ref


def test_uncovered_goals_leave_the_course_not_ready(application, monkeypatch, caplog):
    activity = taught(application)
    initial = DemandBody.model_validate(application.current(LEARNER, "demand").body)
    assert template_gap(initial) is None
    kinds = ("course", "material", "brief", "preview")
    before = {kind: len(application.store.list_current(LEARNER, kind)) for kind in kinds}
    model_changes_goals(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(application.next_course("next1", LEARNER, activity))
    package = result["next_package"]
    assert package["status"] == "not_ready" and package["reason"].startswith("课程未就绪")
    assert "实际观察报告" in package["reason"]  # The catalog name, not the identity.
    assert {kind: len(application.store.list_current(LEARNER, kind)) for kind in kinds} == before
    assert "preparation not_ready" in caplog.text
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        refused = client.post(
            "/api/parent/prepare", headers=parent, json={"request_id": "p2", "learner_id": LEARNER}
        )
    assert refused.status_code == 409 and refused.json()["message"].startswith("课程未就绪")


def model_reviews(application, monkeypatch, **reply):
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        answer = original(request)
        if request.purpose == "planning" and "level" in request.context:
            answer |= {"conclusion": "模型复盘：阅读需要提示，先补实际报告。"} | reply
        return answer

    monkeypatch.setattr(adapter, "respond", respond)


def planning_calls(application):
    return sum(call["purpose"] == "planning" for call in application.models.calls)


def test_week_review_adjusts_the_plan_directly(application, monkeypatch, caplog):
    taught(application)
    patch = {"verification": "下次只看实际报告的条件与结果。"}
    model_reviews(application, monkeypatch, plan_effect="adjusted", patch=patch)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(review(application, "r1", LEARNER, "week"))
    week = application.current(LEARNER, "plan:week")
    assert week.revision == 2 and week.body["verification"] == patch["verification"]
    assert result["plan_effect"] == "adjusted"
    assert result["next_step"] == "recheck_demand_and_course" and "decision_ref" not in result
    saved = application.store.read(RevisionRef.model_validate(result["review_ref"]), LEARNER)
    assert saved.body["conclusion"].startswith("模型复盘")
    assert "review level=week plan_effect=adjusted" in caplog.text


def test_upper_level_changes_wait_for_the_parent(application, monkeypatch):
    taught(application)
    model_reviews(
        application,
        monkeypatch,
        plan_effect="adjusted",
        patch={"main_goal": SCIENCE},
        reason="两次课的阅读都需要提示。",
    )
    result = asyncio.run(review(application, "r1", LEARNER, "stage"))
    proposal = application.store.read(RevisionRef.model_validate(result["decision_ref"]), LEARNER)
    assert proposal.body["decision_state"] == "pending"
    assert proposal.body["patch"] == {"main_goal": SCIENCE}
    assert proposal.body["reason"] == "两次课的阅读都需要提示。"
    assert application.current(LEARNER, "plan:stage").revision == 1
    assert result["plan_effect"] == "reused"


def test_budget_changes_never_apply(application, monkeypatch):
    taught(application)
    patch = {"budget_minutes": 45, "verification": "缩短为两项观察。"}
    model_reviews(application, monkeypatch, plan_effect="adjusted", patch=patch)
    result = asyncio.run(review(application, "r1", LEARNER, "week"))
    week = application.current(LEARNER, "plan:week")
    assert week.body["budget_minutes"] == 30 and week.body["verification"] == "缩短为两项观察。"
    saved = application.store.read(RevisionRef.model_validate(result["review_ref"]), LEARNER)
    assert "预算固定30分钟，改动不生效。" in saved.body["checks"]


def test_review_retry_reuses_the_saved_draft(application, monkeypatch):
    taught(application)
    model_reviews(application, monkeypatch, plan_effect="adjusted", patch={"main_goal": SCIENCE})
    original = application.save_step

    def fail_decision(*args, **kwargs):
        if args[2] == "decision":
            raise OSError("decision save lost")
        return original(*args, **kwargs)

    monkeypatch.setattr(application, "save_step", fail_decision)
    with pytest.raises(OSError):
        asyncio.run(review(application, "r1", LEARNER, "annual"))
    calls = planning_calls(application)
    monkeypatch.setattr(application, "save_step", original)
    result = asyncio.run(review(application, "r1", LEARNER, "annual"))
    assert planning_calls(application) == calls and "decision_ref" in result


def test_a_claimed_adjustment_that_changes_nothing_is_recorded_as_reused(application, monkeypatch):
    activity = taught(application)
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            reply |= {"plan_effect": "adjusted", "plan_patch": {"budget_minutes": 45}}
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    week = application.current(LEARNER, "plan:week")
    result = asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert application.current(LEARNER, "plan:week").ref == week.ref
    assert result["plan_effect"] == "reused"
    assert application.current(LEARNER, "demand").body["plan_effect"] == "reused"
