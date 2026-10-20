import asyncio
import json
from types import SimpleNamespace

import pytest
from support import LEARNER, complete_story, next_course, wait_jobs

from project_j.application.documents import identity
from project_j.application.progress import advance, next_step, retry
from project_j.classroom.judgment import method_context
from project_j.contracts import RevisionRef
from project_j.evaluation.assessment import (
    DraftMismatch,
    EvaluationDraft,
    EvaluationUnavailable,
    TaskReading,
    apply_draft,
    evaluation_request,
    interpret,
)
from project_j.platform.models.simulated import SimulatedAdapter
from project_j.teacher.methods import JANE


def sealed(application, helped=False):
    core, fact = asyncio.run(complete_story(application, helped=helped))
    course = application.store.read(RevisionRef.model_validate(fact.body["course_ref"]), LEARNER)
    return fact, course


def test_evaluation_request_carries_task_data_only(application):
    fact, course = sealed(application, helped=True)
    feedback = [{"text": "无额外补充", "corrections": []}]
    request = evaluation_request(fact.body, course.body, feedback, JANE)
    context = request.context
    assert request.purpose == "evaluation" and request.output is EvaluationDraft
    assert request.deadline_seconds == 180
    assert {a["attempt_id"] for a in context["attempts"]} == {
        a["attempt_id"] for a in fact.body["attempts"]
    }
    assert context["attempts"][0]["answers"] == ["a car"]
    assert context["feedback"] == feedback
    assert context["jane_method"] == method_context(JANE)
    flat = json.dumps(context, ensure_ascii=False)
    assert "fictional-L1" not in flat and "learner_id" not in flat and "profile" not in flat


def test_simulated_evaluation_draft_mirrors_the_rules(application):
    fact, course = sealed(application, helped=True)
    request = evaluation_request(fact.body, course.body, [], JANE)
    draft = EvaluationDraft.model_validate(SimulatedAdapter().respond(request))
    judgments = {t.attempt_id: t.working_judgment for t in draft.tasks}
    for attempt in fact.body["attempts"]:
        helped = any(v not in {"none", "unknown"} for v in attempt["help"].values())
        assert judgments[attempt["attempt_id"]] == ("needs_help" if helped else "unknown")


def base_and_draft(application, helped=False):
    fact, course = sealed(application, helped=helped)
    base = interpret(fact, [], 1, "final")
    readings = [
        TaskReading(
            attempt_id=t["attempt_id"],
            working_judgment="unknown",
            external_help="unknown",
            explanation="说明。",
        )
        for t in base["tasks"]
    ]
    draft = EvaluationDraft(
        tasks=tuple(readings), summary="概括", strongest=(), uncertain=(), alignment="差异"
    )
    return fact, base, draft


def reading(draft, index, **update):
    tasks = list(draft.tasks)
    tasks[index] = tasks[index].model_copy(update=update)
    return draft.model_copy(update={"tasks": tuple(tasks)})


def index_of(base, task_id):
    return next(i for i, t in enumerate(base["tasks"]) if t["task_id"] == task_id)


def test_quoted_no_help_allows_single_independent(application):
    fact, base, draft = base_and_draft(application)  # first answer "a bridge", no help
    first = index_of(base, "reading-fact")
    draft = reading(
        draft,
        first,
        working_judgment="single_independent",
        external_help="none",
        help_quote="我没有帮忙",
    )
    merged = apply_draft(base, draft, [{"text": "我没有帮忙，他自己读的。", "corrections": []}])
    task = merged["tasks"][first]
    assert task["working_judgment"] == "single_independent" and task["external_help"] == "none"
    assert task["help"]["english.reading.facts"] == "none" and merged["checks"] == []
    assert merged["report"]["summary"] == "概括" and merged["basis_version"] == "evaluation-draft-1"
    assert base["tasks"][first]["working_judgment"] == "unknown"  # the base is not modified


def test_unquoted_external_help_stays_unknown(application):
    fact, base, draft = base_and_draft(application)
    draft = reading(
        draft,
        0,
        working_judgment="single_independent",
        external_help="none",
        help_quote="我没有帮忙",
    )
    merged = apply_draft(base, draft, [{"text": "无额外补充", "corrections": []}])
    assert merged["tasks"][0]["external_help"] == "unknown"
    assert merged["tasks"][0]["working_judgment"] == "unknown"
    assert [c["reason"] for c in merged["checks"]] == [
        "外部帮助未逐字引用学生补记，按未知处理",
        "单次独立的条件不足",
    ]


def test_rule_violations_fall_back_to_unknown(application):
    fact, base, draft = base_and_draft(application, helped=True)
    wrong = index_of(base, "reading-fact")  # first attempt "a car": incorrect
    plain = index_of(base, "reading-sequence")  # correct, no help
    draft = reading(draft, wrong, working_judgment="single_independent")
    draft = reading(draft, plain, working_judgment="needs_help")
    merged = apply_draft(base, draft, [])
    assert merged["tasks"][wrong]["working_judgment"] == "unknown"
    assert merged["tasks"][plain]["working_judgment"] == "unknown"
    assert "需要帮助缺少依据" in merged["tasks"][plain]["limitations"]
    assert {c["task_id"] for c in merged["checks"]} == {"reading-fact", "reading-sequence"}


def test_reported_help_supports_needs_help(application):
    fact, base, draft = base_and_draft(application)
    draft = reading(
        draft,
        0,
        working_judgment="needs_help",
        external_help="reported",
        help_quote="第一题是我提示的",
    )
    merged = apply_draft(base, draft, [{"text": "第一题是我提示的。", "corrections": []}])
    assert merged["tasks"][0]["working_judgment"] == "needs_help" and merged["checks"] == []


def test_corrected_task_is_unknown(application):
    fact, base, draft = base_and_draft(application)
    feedback = {
        "text": "第一题其实是我代答的。",
        "corrections": [{"task_id": base["tasks"][0]["task_id"]}],
    }
    base = interpret(fact, [SimpleNamespace(body=feedback, ref=fact.ref)], 2, "final")
    draft = reading(
        draft,
        0,
        working_judgment="needs_help",
        external_help="reported",
        help_quote="其实是我代答的",
    )
    merged = apply_draft(base, draft, [feedback])
    assert merged["tasks"][0]["working_judgment"] == "unknown"
    assert "受学生更正影响，待复核" in merged["tasks"][0]["limitations"]


def test_draft_with_missing_attempt_is_rejected(application):
    fact, base, draft = base_and_draft(application)
    with pytest.raises(DraftMismatch):
        apply_draft(base, draft.model_copy(update={"tasks": draft.tasks[1:]}), [])


def evaluation_calls(application):
    return sum(call["purpose"] == "evaluation" for call in application.models.calls)


def test_next_course_saves_a_checked_draft(application):
    core, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "无额外补充", [])
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    evaluation = application.store.current(identity(activity, "evaluation"), LEARNER)
    assert evaluation.body["basis_version"] == "evaluation-draft-1"
    assert evaluation.body["report"]["summary"].startswith("模拟评价")
    assert evaluation.body["draft"]["tasks"]
    assert result["next_package"]  # downstream unchanged


def test_draft_with_wrong_attempts_is_not_saved(application, monkeypatch):
    core, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "孩子说很好玩。", [])
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def drop_one(request):
        reply = original(request)
        return reply | {"tasks": reply["tasks"][1:]} if request.purpose == "evaluation" else reply

    monkeypatch.setattr(adapter, "respond", drop_one)
    with pytest.raises(EvaluationUnavailable, match="尝试不一致"):
        asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))
    assert application.store.current(identity(activity, "evaluation"), LEARNER) is None
    monkeypatch.setattr(adapter, "respond", original)
    assert asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))


def test_evaluation_failure_is_reported_and_retryable(application):
    async def scenario():
        _, fact = await complete_story(application)
        application.models.adapters["simulated"].fail_next = True
        name = advance(application, LEARNER)
        assert name.startswith("evaluate:")
        await wait_jobs(application)
        assert application.job_failures[name] == (
            "课后评价未取得（模型暂不可用）。事实与反馈已保存，可以重试。"
        )
        retry(application, LEARNER, name)
        await wait_jobs(application)
        assert next_step(application, LEARNER)["kind"] == "evaluation"

    asyncio.run(scenario())


def test_no_new_facts_reuses_the_prior_draft_without_a_call(application):
    core, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "无额外补充", [])
    first = asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))
    calls = evaluation_calls(application)
    application.feedback("fb2", LEARNER, activity, "无额外补充", [])
    second = asyncio.run(application.evaluate("e2", LEARNER, activity, "final_feedback"))
    assert evaluation_calls(application) == calls
    assert second.revision == first.revision + 1
    assert second.body["draft"] == first.body["draft"]


def test_evaluation_sees_the_chosen_skills(application):
    fact, course = sealed(application)
    assert evaluation_request(fact.body, course.body, [], JANE).context["skill_focus"] == []
    ticked = {
        "field": "skill_focus",
        "requirement": "一般过去时：过去发生的事",
        "implementation": "x",
    }
    body = course.body | {"demand_mapping": [*course.body["demand_mapping"], ticked]}
    request = evaluation_request(fact.body, body, [], JANE)
    assert request.context["skill_focus"] == ["一般过去时：过去发生的事"]
    assert "勾选的加强项目" in request.instructions
