import asyncio
import json
from types import SimpleNamespace

import pytest
from support import LEARNER, complete_story, next_course, wait_jobs

from project_j.application.documents import identity, successor
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
from project_j.platform.storage import Publication, SaveKey
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
    assert request.deadline_key == "evaluation"
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
        "Outside help was not quoted word for word from your notes; unknown",
        "Not enough to call it done alone",
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
    assert "No evidence that help was needed" in merged["tasks"][plain]["limitations"]
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
    assert "Your correction affects it; to be checked again" in merged["tasks"][0]["limitations"]


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
    assert evaluation.body["report"]["summary"].startswith("Simulated evaluation")
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
    with pytest.raises(EvaluationUnavailable, match="does not match the tries"):
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
            "The evaluation could not be made (the model is not available now). "
            "The class record and your notes are saved; you can try again."
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
        "requirement": "Past simple: things that happened",
        "implementation": "x",
    }
    body = course.body | {"demand_mapping": [*course.body["demand_mapping"], ticked]}
    request = evaluation_request(fact.body, body, [], JANE)
    assert request.context["skill_focus"] == ["Past simple: things that happened"]
    assert "勾选的加强项目" in request.instructions


def scored(base, draft, *readings):
    return apply_draft(
        base, draft.model_copy(update={"scores": readings}) if readings else draft, []
    )


def entry(merged, task_id):
    return next(item for item in merged["scores"] if item["task_id"] == task_id)


def test_the_evaluation_scores_every_scored_question(application):
    fact, course = sealed(application)
    request = evaluation_request(fact.body, course.body, [], JANE)
    draft = EvaluationDraft.model_validate(SimulatedAdapter().respond(request))
    merged = apply_draft(interpret(fact, [], 1, "final"), draft, [])
    basis = {item["task_id"]: item["basis"] for item in merged["scores"]}
    assert {k for k, v in basis.items() if v == "rule"} == {
        "choice-first-try", "order-bridge", "match-words", "fill-puts"
    }  # fmt: skip
    assert {k for k, v in basis.items() if v == "model"} == {
        "reading-fact", "reading-sequence", "retell-bridge"
    }  # fmt: skip
    assert all(v == "pronunciation" for k, v in basis.items() if k.startswith("read-aloud-"))
    assert "point-paper" not in basis and "paper-experiment" not in basis
    assert all(item["score"] == 10 for item in merged["scores"] if item["basis"] != "pronunciation")
    reading = next(item for item in merged["scores"] if item["basis"] == "pronunciation")
    assert (
        reading["limitations"] == ["The pronunciation score limits are not calibrated."]
        and reading["score"] is not None
    )
    assert merged["checks"] == []


def test_evaluation_request_reads_clicked_answers_as_text(application):
    fact, course = sealed(application)
    context = evaluation_request(fact.body, course.body, [], JANE).context
    choice = next(a for a in context["attempts"] if a["task_id"] == "choice-first-try")
    task = next(t for t in course.body["tasks"] if t["task_id"] == "choice-first-try")
    assert choice["answers"] == [task["choice"]["options"][task["choice"]["correct"]]]
    shown = next(t for t in context["tasks"] if t["task_id"] == "choice-first-try")
    assert shown["content"] == task["choice"]
    assert "content" not in next(t for t in context["tasks"] if t["task_id"] == "reading-fact")
    assert "scores只给tasks里purpose为answer或open的题" in request_instructions()


def request_instructions():
    from project_j.evaluation.assessment import EVALUATION_INSTRUCTIONS

    return EVALUATION_INSTRUCTIONS


def test_model_scores_are_checked(application):
    from project_j.evaluation.assessment import ScoreReading

    fact, base, draft = base_and_draft(application, helped=True)  # reading-fact had a wrong try.
    merged = scored(
        base,
        draft,
        ScoreReading(task_id="reading-sequence", score=11, reason="Above full"),
        ScoreReading(task_id="reading-fact", score=10, reason="Full"),
        ScoreReading(task_id="no-such-task", score=5, reason="Extra"),
    )
    assert entry(merged, "reading-sequence")["score"] is None
    assert entry(merged, "reading-sequence")["reason"] == "Score out of range"
    assert entry(merged, "reading-fact")["reason"] == "No 10 after a wrong try"
    assert entry(merged, "retell-bridge") | {"score": None} == entry(merged, "retell-bridge")
    assert entry(merged, "retell-bridge")["reason"] == "The evaluation gave no score for it"
    assert {(c["task_id"], c["reason"]) for c in merged["checks"]} >= {
        ("reading-sequence", "The score was out of range, so it stays unknown"),
        ("reading-fact", "A 10 after a wrong try is not allowed; unknown"),
        ("no-such-task", "Not a scored question"),
    }
    fine = scored(
        base, draft, ScoreReading(task_id="reading-fact", score=7, reason="Right after 1 wrong try")
    )
    assert (entry(fine, "reading-fact")["score"], entry(fine, "reading-fact")["reason"]) == (
        7,
        "Right after 1 wrong try",
    )


def test_a_draft_from_before_scoring_is_reused_without_failing(application):
    core, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "无额外补充", [])
    first = asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))
    old = dict(first.body["draft"])
    del old["scores"]  # As saved before scoring.
    earlier = successor(first, "old-draft", first.body | {"draft": old}, refs=first.dependency_refs)
    application.store.save(
        SaveKey(operation_id="old-draft", step_id="evaluation", source_revision=1),
        LEARNER,
        [earlier],
        (Publication(ref=earlier.ref, expected_revision=first.revision, reason="测试：旧草稿"),),
    )
    application.feedback("fb2", LEARNER, activity, "无额外补充", [])
    calls = evaluation_calls(application)
    second = asyncio.run(application.evaluate("e2", LEARNER, activity, "final_feedback"))
    assert evaluation_calls(application) == calls  # Reused, not asked again.
    model = [item for item in second.body["scores"] if item["basis"] == "model"]
    assert model and all(item["score"] is None for item in model)
    assert {item["reason"] for item in model} == {
        "The evaluation was written before scores existed"
    }
    assert any(
        item["score"] is not None for item in second.body["scores"] if item["basis"] == "rule"
    )


def test_corrections_make_the_score_unknown(application):
    fact, base, draft = base_and_draft(application)
    feedback = {"text": "选择题是我代答的。", "corrections": [{"task_id": "choice-first-try"}]}
    base = interpret(fact, [SimpleNamespace(body=feedback, ref=fact.ref)], 2, "final")
    merged = apply_draft(base, draft, [feedback])
    assert entry(merged, "choice-first-try")["score"] is None
    assert (
        entry(merged, "choice-first-try")["reason"]
        == "Your correction affects it; to be checked again"
    )
    assert entry(merged, "order-bridge")["score"] == 10


def test_evaluation_request_carries_the_reading_level(application):
    fact, course = sealed(application)
    for level in ("500L", None):
        request = evaluation_request(fact.body, course.body, [], JANE, level)
        assert request.context["reading_level"] == level
    assert "reading_level" in request.instructions and "初学者" in request.instructions


def test_an_evaluation_draft_with_chinese_fails_and_can_retry(application, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="project_j")
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def chinese_summary(request):
        reply = original(request)
        return reply | {"summary": "学得不错。"} if request.purpose == "evaluation" else reply

    monkeypatch.setattr(adapter, "respond", chinese_summary)
    with pytest.raises(EvaluationUnavailable, match="the reply was not in English"):
        asyncio.run(application.evaluate("e1", LEARNER, activity, "after_class"))
    assert application.store.current(identity(activity, "evaluation"), LEARNER) is None
    assert "failure=language" in caplog.text and "学得不错" not in caplog.text
    monkeypatch.setattr(adapter, "respond", original)
    assert asyncio.run(application.evaluate("e1", LEARNER, activity, "after_class"))


def test_a_chinese_feedback_quote_does_not_fail_the_evaluation(application, monkeypatch):
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    quote = "妈妈帮我读了第一段"
    application.feedback("fb1", LEARNER, activity, quote, [])
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def quoting(request):
        reply = original(request)
        if request.purpose != "evaluation":
            return reply
        first = reply["tasks"][0] | {"external_help": "reported", "help_quote": quote}
        return reply | {"tasks": [first, *reply["tasks"][1:]]}

    monkeypatch.setattr(adapter, "respond", quoting)
    evaluation = asyncio.run(application.evaluate("e1", LEARNER, activity, "final_feedback"))
    assert evaluation.body["tasks"][0]["help_quote"] == quote
