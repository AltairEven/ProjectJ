import asyncio
import json
import logging

import pytest
from support import LEARNER, complete_story, next_course, wait_jobs

from project_j.application.documents import identity
from project_j.application.progress import advance, retry
from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody
from project_j.learning.maintenance import review
from project_j.learning.planning import (
    PLANNING_INSTRUCTIONS,
    DemandDraft,
    PlanningUnavailable,
    ReviewDraft,
    apply_demand_draft,
    demand_request,
    difficulty_step,
    review_request,
)
from project_j.platform.models.simulated import SimulatedAdapter
from project_j.preparation.drafting import PreparationUnavailable

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
    evaluation = application.store.current(identity(activity, "evaluation"), LEARNER).body
    course_ref = RevisionRef.model_validate(evaluation["alignment"]["tasks_materials"])
    return {
        "evaluation": evaluation,
        "course": application.store.read(course_ref, LEARNER).body,
        "state": application.current(LEARNER, "state").body,
        "plans": {level: application.current(LEARNER, f"plan:{level}").body for level in LEVELS},
        "profile": application.store.read(
            RevisionRef.model_validate(demand.body["profile_ref"]), LEARNER
        ).body,
        "catalog": [doc.body for doc in application.store.list_current(LEARNER, "capability")],
        "suggestion": application.store.current(identity(activity, "suggestion"), LEARNER).body,
        "demand": demand.body,
        "recent_titles": ["The Paper Bridge"],
    }


def requests(application, activity):
    data = bodies(application, activity)
    demand = demand_request(**data)
    review = review_request(
        "week",
        data["plans"],
        data["state"],
        [data["evaluation"]],
        data["profile"],
        data["catalog"],
    )
    return data, demand, review


def test_demand_request_carries_learning_data(application):
    data, request, _ = requests(application, taught(application))
    context = request.context
    assert request.purpose == "planning" and request.output is DemandDraft
    assert request.deadline_key == "planning"
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


def test_planning_reads_the_recent_scores(application, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="project_j")
    data, request, _ = requests(application, taught(application))
    assert request.context["scores"] == {}  # None given: the request still stands.
    from project_j.application.progress import lesson_score_summary

    scores = lesson_score_summary(application, LEARNER, "planning")
    assert scores["lessons"] == 1 and scores["by_type"]["choice"]["average"] == 10.0
    with_scores = demand_request(**data, scores=scores)
    text = json.dumps(with_scores.context["scores"], ensure_ascii=False)
    assert "Mia" not in text and "reason" not in text and "Simulated score" not in text
    assert "scores是最近几节课按题型与能力的平均分" in with_scores.instructions
    assert "planning scores lessons=1 types=" in caplog.text


def test_requests_carry_no_identity(application):
    _, demand, review = requests(application, taught(application))
    assert review.purpose == "planning" and review.output is ReviewDraft
    # The real model once read Jane as the learner; the role is stated in both instructions.
    assert all("Jane是唯一的AI教师，不是学生" in r.instructions for r in (demand, review))
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


SCIENCE = {
    "dimension_id": "science.observation.report",
    "description": "Say what really happened to the paper bridge",
}
REPORT = {
    "dimension_id": "english.speaking.report",
    "description": "Report the result in short English",
}


def model_changes_goals(application, monkeypatch, primary=SCIENCE, adjust=True):
    """The planning model moves the next lesson to science observation."""
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            reply |= {
                "suggestion_action": "modify",
                "suggestion_reason": "Model reason: reading needed hints; observe a report next.",
                "direction": "extend",
                "primary_goal": primary,
                "auxiliary_goal": REPORT,
                "plan_effect": "adjusted" if adjust else "reused",
                "plan_reason": "This week first adds samples of real reports.",
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
    assert demand.body["primary_goal"] == SCIENCE and demand.body["direction"] == "extend"
    assert demand.body["note"]["after"]
    choice = demand.body["suggestion_choices"][0]
    assert choice["action"] == "modify" and choice["reason"].startswith("Model reason")
    assert week.body["main_goal"] == SCIENCE and week.revision == 2
    assert demand.body["plan_ref"] == week.ref.model_dump()
    assert (
        result["plan_effect"] == "adjusted"
        and result["plan_reason"] == "This week first adds samples of real reports."
    )
    assert f"planning activity={activity}" in caplog.text and "孩子说很好玩" not in caplog.text


def test_unknown_goal_rejects_the_draft_and_retry_succeeds(application, monkeypatch):
    activity = taught(application)
    demand, week = application.current(LEARNER, "demand"), application.current(LEARNER, "plan:week")
    model_changes_goals(
        application, monkeypatch, {"dimension_id": "math.counting", "description": "Counting"}
    )
    with pytest.raises(PlanningUnavailable, match="is not in the skill list"):
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

    async def scenario():
        evaluation = await application.evaluate("e1", LEARNER, activity, "after_class")
        application.acknowledge("ack1", LEARNER, activity, evaluation.ref)
        application.models.adapters["simulated"].fail_next = True
        name = advance(application, LEARNER)
        assert name.startswith("adopt:")
        await wait_jobs(application)
        assert application.job_failures[name].startswith(
            "The next lesson's plan could not be made (the model is not available now)"
        )
        state = application.current(LEARNER, "state")
        retry(application, LEARNER, name)
        await wait_jobs(application)
        assert application.current(LEARNER, "state").ref == state.ref
        assert evaluation.ref in application.current(LEARNER, "demand").source_refs

    asyncio.run(scenario())


def test_uncovered_goals_leave_the_course_not_ready(application, monkeypatch, caplog):
    """The simulated model only reproduces the paper-bridge template; science goals fail."""
    activity = taught(application)
    kinds = ("course", "material", "brief", "preview")
    before = {kind: len(application.store.list_current(LEARNER, kind)) for kind in kinds}
    model_changes_goals(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    package = result["next_package"]
    assert package["status"] == "not_ready"
    assert package["reason"] == (
        "The lesson could not be made (the model is not available now). "
        "The next lesson's plan is saved; you can try again."
    )
    assert {kind: len(application.store.list_current(LEARNER, kind)) for kind in kinds} == before
    assert "preparation failure stage=prepare failure=unavailable" in caplog.text
    with pytest.raises(PreparationUnavailable, match="^The lesson could not be made"):
        asyncio.run(application.prepare("p2", LEARNER))


def model_reviews(application, monkeypatch, **reply):
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        answer = original(request)
        if request.purpose == "planning" and "level" in request.context:
            answer |= {
                "conclusion": "Model review: reading needed hints; add real reports first."
            } | reply
        return answer

    monkeypatch.setattr(adapter, "respond", respond)


def planning_calls(application):
    return sum(call["purpose"] == "planning" for call in application.models.calls)


def test_week_review_adjusts_the_plan_directly(application, monkeypatch, caplog):
    taught(application)
    patch = {"verification": "Next time only check the conditions and results of real reports."}
    model_reviews(application, monkeypatch, plan_effect="adjusted", patch=patch)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(review(application, "r1", LEARNER, "week"))
    week = application.current(LEARNER, "plan:week")
    assert week.revision == 2 and week.body["verification"] == patch["verification"]
    assert result["plan_effect"] == "adjusted"
    assert result["next_step"] == "recheck_demand_and_course" and "decision_ref" not in result
    saved = application.store.read(RevisionRef.model_validate(result["review_ref"]), LEARNER)
    assert saved.body["conclusion"].startswith("Model review")
    assert "review level=week plan_effect=adjusted" in caplog.text


def test_upper_level_changes_wait_for_the_parent(application, monkeypatch):
    taught(application)
    model_reviews(
        application,
        monkeypatch,
        plan_effect="adjusted",
        patch={"main_goal": SCIENCE},
        reason="Reading needed hints in both classes.",
    )
    result = asyncio.run(review(application, "r1", LEARNER, "stage"))
    proposal = application.store.read(RevisionRef.model_validate(result["decision_ref"]), LEARNER)
    assert proposal.body["decision_state"] == "pending"
    assert proposal.body["patch"] == {"main_goal": SCIENCE}
    assert proposal.body["reason"] == "Reading needed hints in both classes."
    assert application.current(LEARNER, "plan:stage").revision == 1
    assert result["plan_effect"] == "reused"


def test_budget_changes_never_apply(application, monkeypatch):
    taught(application)
    patch = {"budget_minutes": 45, "verification": "Shorten to two observations."}
    model_reviews(application, monkeypatch, plan_effect="adjusted", patch=patch)
    result = asyncio.run(review(application, "r1", LEARNER, "week"))
    week = application.current(LEARNER, "plan:week")
    assert (
        week.body["budget_minutes"] == 30
        and week.body["verification"] == "Shorten to two observations."
    )
    saved = application.store.read(RevisionRef.model_validate(result["review_ref"]), LEARNER)
    assert "The time is fixed at 30 minutes; the change does not apply." in saved.body["checks"]


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


def test_planning_suggests_at_most_two_known_skills(application, monkeypatch):
    activity = taught(application)
    adapter = application.models.adapters["simulated"]
    original, seen = adapter.respond, []

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            seen.append(request)
            reply |= {
                "focus_suggestions": [
                    {"skill_id": "unknown", "reason": "Not on the list"},
                    {"skill_id": "past_simple", "reason": "Two past verbs were in the base form"},
                    {"skill_id": "articles", "reason": "Missed articles"},
                    {"skill_id": "verbs", "reason": "A third one"},
                ]
            }
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    demand = application.current(LEARNER, "demand")
    kept = [item["skill_id"] for item in demand.body["focus_suggestions"]]
    assert kept == ["past_simple", "articles"] and demand.body["skill_focus"] is None
    assert len(seen[0].context["skills"]) == 13 and "0–2项" in seen[0].instructions


def test_planning_lists_topics_for_the_student(application, monkeypatch, caplog):
    activity = taught(application)
    adapter = application.models.adapters["simulated"]
    original, seen = adapter.respond, []
    topics = [{"title": f"Zelda angle {n}", "reason": f"Interest {n}"} for n in range(5)]

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            seen.append(request)
            reply |= {"topic_suggestions": topics}
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    demand = application.current(LEARNER, "demand").body
    assert demand["topic_suggestions"] == topics[:4] and demand["topic"] is None
    assert seen[0].context["recent_titles"] == ["The Paper Bridge"]
    assert "topic_suggestions" in seen[0].instructions and "3–4个" in seen[0].instructions
    assert "topics=4" in caplog.text
    assert not any(item["title"] in caplog.text or item["reason"] in caplog.text for item in topics)


def test_simulated_planning_suggests_the_next_library_story(application):
    activity = taught(application)
    asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    suggestions = application.current(LEARNER, "demand").body["topic_suggestions"]
    assert [item["title"] for item in suggestions] == ["Ben’s Bridge"]


def test_a_long_topic_is_dropped_without_voiding_the_plan(application, monkeypatch):
    from project_j.learning.planning import DemandDraft

    # Output schemas so far carry no length limits; a stray long title must not need one.
    assert "maxLength" not in json.dumps(DemandDraft.model_json_schema())
    activity = taught(application)
    adapter = application.models.adapters["simulated"]
    original = adapter.respond
    topics = [
        {"title": "L" * 101, "reason": "Too long"},
        {"title": "Volcanoes", "reason": "Interest"},
    ]

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            reply |= {"topic_suggestions": topics}
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert application.current(LEARNER, "demand").body["topic_suggestions"] == topics[1:]


def test_a_plan_with_chinese_fails(application, monkeypatch, caplog):
    from project_j.learning.planning import PlanningUnavailable

    caplog.set_level(logging.INFO, logger="project_j")
    activity = taught(application)
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def chinese_plan(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            return reply | {"plan_reason": "沿用。"}
        return reply

    monkeypatch.setattr(adapter, "respond", chinese_plan)
    with pytest.raises(PlanningUnavailable, match="the reply was not in English"):
        asyncio.run(next_course(application, "n1", LEARNER, activity))
    assert "failure=language" in caplog.text and "沿用。" not in caplog.text


def test_a_plan_review_with_chinese_fails(application, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    taught(application)
    model_reviews(application, monkeypatch, conclusion="先补实际报告。")
    with pytest.raises(PlanningUnavailable, match="the reply was not in English"):
        asyncio.run(review(application, "r1", LEARNER, "week"))
    assert application.current(LEARNER, "plan:week").revision == 1
    assert "review level=week failure=language" in caplog.text
    assert "先补实际报告" not in caplog.text


# Difficulty up or down from the lesson's main-goal questions (ADR-0030).
FACTS, SPEAKING = "english.reading.facts", "english.speaking.report"
PRONUNCIATION = "english.speaking.pronunciation"


def lesson(primary, tasks, scores, tries):
    """A course and its evaluation reduced to what the rule reads: tasks (id, dimension,
    purpose), scores by task id, and each try's working judgment by task id."""
    course = {
        "primary_goal": {"dimension_id": primary},
        "tasks": [{"task_id": i, "dimension_id": d, "purpose": p} for i, d, p in tasks],
    }
    evaluation = {
        "scores": [{"task_id": i, "score": score} for i, score in scores.items()],
        "tasks": [
            {"task_id": i, "working_judgment": judgment}
            for i, judgments in tries.items()
            for judgment in judgments
        ],
    }
    return evaluation, course


TWO_ANSWERS = [("a", FACTS, "answer"), ("b", FACTS, "choice")]
ALONE = "single_independent"


def test_all_independent_main_goal_tasks_step_up():
    step = difficulty_step(
        *lesson(FACTS, TWO_ANSWERS, {"a": 10, "b": 10}, {"a": [ALONE], "b": [ALONE]})
    )
    assert step == ("up", "all_independent")


def test_a_low_average_steps_down():
    tries = {"a": ["needs_help"], "b": ["needs_help"]}
    assert difficulty_step(*lesson(FACTS, TWO_ANSWERS, {"a": 5, "b": 6}, tries)) == (
        "down",
        "low_average",
    )


def test_six_is_not_low():
    tries = {"a": ["needs_help"], "b": ["needs_help"]}
    assert difficulty_step(*lesson(FACTS, TWO_ANSWERS, {"a": 6, "b": 6}, tries)) == (
        None,
        "between",
    )


def test_a_wrong_first_try_then_right_does_not_step_up():
    tries = {"a": [ALONE], "b": ["needs_help", "needs_help"]}
    assert difficulty_step(*lesson(FACTS, TWO_ANSWERS, {"a": 10, "b": 7}, tries)) == (
        None,
        "between",
    )


def test_a_task_never_started_keeps_the_rule_silent():
    tasks = [*TWO_ANSWERS, ("c", FACTS, "fill")]
    tries = {"a": [ALONE], "b": [ALONE]}
    step = difficulty_step(*lesson(FACTS, tasks, {"a": 10, "b": 10}, tries))
    assert step == (None, "unknown_scores")


def test_an_unknown_score_keeps_the_rule_silent():
    tries = {"a": [ALONE], "b": [ALONE]}
    step = difficulty_step(*lesson(FACTS, TWO_ANSWERS, {"a": 10, "b": None}, tries))
    assert step == (None, "unknown_scores")


def test_an_evaluation_without_scores_keeps_the_rule_silent():
    evaluation, course = lesson(FACTS, TWO_ANSWERS, {}, {"a": [ALONE], "b": [ALONE]})
    del evaluation["scores"]  # Saved before scoring existed (ADR-0020).
    assert difficulty_step(evaluation, course) == (None, "unknown_scores")


def test_point_and_offscreen_do_not_count():
    science = "science.observation.report"
    tasks = [("p", science, "point"), ("o", science, "offscreen")]
    step = difficulty_step(*lesson(science, tasks, {}, {"p": ["unknown"], "o": ["unknown"]}))
    assert step == (None, "no_scored_tasks")


def test_low_average_wins_over_all_independent():
    tasks = [("r1", PRONUNCIATION, "read_aloud"), ("r2", PRONUNCIATION, "read_aloud")]
    tries = {"r1": [ALONE], "r2": [ALONE]}
    step = difficulty_step(*lesson(PRONUNCIATION, tasks, {"r1": 5.0, "r2": 5.5}, tries))
    assert step == ("down", "low_average")


def test_other_dimensions_do_not_count():
    tasks = [*TWO_ANSWERS, ("s", SPEAKING, "open")]
    tries = {"a": [ALONE], "b": [ALONE], "s": ["needs_help"]}
    step = difficulty_step(*lesson(FACTS, tasks, {"a": 10, "b": 10, "s": 2}, tries))
    assert step == ("up", "all_independent")


def drafted(application, direction):
    data = bodies(application, taught(application))
    reply = SimulatedAdapter().respond(demand_request(**data))
    return data, DemandDraft.model_validate(reply | {"direction": direction})


def checked(data, draft, step):
    current = DemandBody.model_validate(data["demand"])
    known = {item["dimension_id"] for item in data["catalog"]}
    ref = RevisionRef(object_id="x", revision=1)
    return apply_demand_draft(draft, current, known, ref, ref, ref, plan_adjusted=False, step=step)


def test_step_up_refuses_a_direction_that_does_not_raise(application):
    data, draft = drafted(application, "consolidate")
    with pytest.raises(PlanningUnavailable, match="difficulty rule"):
        checked(data, draft, "up")


def test_step_down_refuses_a_raising_direction(application):
    data, draft = drafted(application, "advance")
    with pytest.raises(PlanningUnavailable, match="difficulty rule"):
        checked(data, draft, "down")


def test_no_step_allows_any_direction(application):
    data, draft = drafted(application, "advance")
    assert checked(data, draft, None).direction == "advance"


def test_demand_request_carries_the_difficulty_and_lesson_tasks(application):
    data = bodies(application, taught(application))
    context = demand_request(**data).context
    assert context["difficulty"] == {"step": "up", "reason": "all_independent"}
    tasks = context["lesson_tasks"]
    assert tasks and all(
        set(item) == {"task_id", "dimension_id", "purpose", "variant"} for item in tasks
    )
    text = json.dumps(tasks, ensure_ascii=False)
    assert all(task["prompt"] not in text for task in data["course"]["tasks"])


def test_simulated_planning_raises_after_an_all_right_lesson(application, caplog):
    activity = taught(application)
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert application.current(LEARNER, "demand").body["direction"] == "advance"
    assert f"planning activity={activity} difficulty=up reason=all_independent" in caplog.text
    assert "direction=advance" in caplog.text  # The saved plan's direction, which obeyed it.


def test_a_direction_against_the_rule_is_logged_and_not_saved(application, monkeypatch, caplog):
    activity = taught(application, feedback="No one helped me.")
    demand = application.current(LEARNER, "demand")
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        reply = original(request)
        if request.purpose == "planning" and "demand" in request.context:
            reply |= {"direction": "consolidate"}
        return reply

    monkeypatch.setattr(adapter, "respond", respond)
    caplog.set_level(logging.INFO, logger="project_j")
    with pytest.raises(PlanningUnavailable, match="difficulty rule"):
        asyncio.run(application.adopt("adopt1", LEARNER, evaluation_of(application, activity)))
    assert application.current(LEARNER, "demand").ref == demand.ref
    assert "difficulty=up" in caplog.text
    rejected = [r.getMessage() for r in caplog.records if "rejected:" in r.getMessage()]
    assert rejected and "the direction does not follow the difficulty rule" in rejected[0]
    assert "No one helped me." not in caplog.text


def test_planning_instructions_ask_for_targets_not_question_lists():
    assert "不写具体题目、题干、题数或题型分布" in PLANNING_INSTRUCTIONS
    assert "difficulty优先于suggestion里的方向" in PLANNING_INSTRUCTIONS
