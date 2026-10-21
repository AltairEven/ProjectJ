"""Per task, a score out of 10 after class (ADR-0020); unknown is never 0.

Each task type registers how it scores: by the rule formula, by the evaluation model looking at
all its answers, from the pronunciation scorer, or not at all. The three-level judgment of the
evaluation stays as it was and alone moves the learning state.
"""

from typing import Literal, NamedTuple

SCORING: dict[str, Literal["rule", "model", "pronunciation"] | None] = {
    "answer": "model",
    "open": "model",
    "choice": "rule",
    "order": "rule",
    "match": "rule",
    "fill": "rule",
    "read_aloud": "pronunciation",
    "point": None,
    "offscreen": None,
}
# Right at once, after 1 wrong answer, after 2; after 3 or more it is 0 (user, 2026-10-21).
RULE_POINTS = (10, 7, 4)
HINT_PENALTY = 1  # Per "给我提示".
MISREAD_PENALTY = 1  # Per earlier misread segment.
UNCALIBRATED = "发音评分门槛未校准。"
NO_ANSWER = "没有作答"
UNFINISHED = "停课时这题还没完成"
CORRECTED = "受学生更正影响，待复核"
AWAITING = "等待课后评价模型打分"
NO_MODEL_SCORE = "评价模型没有给这题打分"
EARLIER_DRAFT = "评价草稿早于计分"


class Question(NamedTuple):
    task_id: str
    task_instance_id: str
    purpose: str
    dimension_id: str
    attempts: list[dict]
    hints: int
    exited: bool
    skipped: bool


def questions(fact: dict) -> list[Question]:
    """One per task instance the class started, with its attempts, hints and way out."""
    events = fact["events"]

    def instances(kind):
        return [e["payload"].get("task_instance_id") for e in events if e["kind"] == kind]

    pleas = [
        e["payload"]["task_instance_id"]
        for e in events
        if e["kind"] == "input" and e["payload"].get("intent") == "help"
    ]
    exited, skipped = set(instances("task_exited")), set(instances("task_skipped"))
    return [
        Question(
            task_id=started["task_id"],
            task_instance_id=(instance := started["task_instance_id"]),
            purpose=started["purpose"],
            dimension_id=started["dimension_id"],
            attempts=[a for a in fact["attempts"] if a["task_instance_id"] == instance],
            hints=pleas.count(instance),
            exited=instance in exited,
            skipped=instance in skipped,
        )
        for started in (e["payload"] for e in events if e["kind"] == "task_started")
    ]


def finished(question: Question) -> bool:
    """Done with, not cut off: answered right, helped out or skipped."""
    return (
        question.exited
        or question.skipped
        or any(a["judgment"] in {"correct", "reported"} for a in question.attempts)
    )


def rule_score(question: Question) -> tuple[int | None, str]:
    if not question.attempts:
        return None, NO_ANSWER
    judgments = [attempt["judgment"] for attempt in question.attempts]
    if "correct" in judgments:
        wrong = judgments[: judgments.index("correct")].count("incorrect")
        points = RULE_POINTS[wrong] if wrong < len(RULE_POINTS) else 0
        reason = "一次答对" if not wrong else f"错{wrong}次后答对"
        if question.hints:
            reason += f"，用了{question.hints}次提示"
        return max(0, points - HINT_PENALTY * question.hints), reason
    if question.exited:
        return 0, "帮助用完仍没答对"
    if question.skipped:
        return 0, "答过后跳过，没有答对"
    return None, UNFINISHED  # Still current when the class stopped or closed.


def pronunciation_score(question: Question) -> tuple[float | None, str]:
    """The last scored try, less one per earlier misread; the scorer is not calibrated."""
    scored = [
        index
        for index, attempt in enumerate(question.attempts)
        if (attempt.get("pronunciation") or {}).get("status") == "scored"
    ]
    if not scored:
        return None, "没有取得朗读评分"
    if not finished(question):
        return None, UNFINISHED
    last = question.attempts[scored[-1]]["pronunciation"]
    misread = [a["judgment"] for a in question.attempts[: scored[-1]]].count("incorrect")
    score = round(last["accuracy"] / 10 * last["completeness"] / 100, 1)
    reason = "按最后一次有分的朗读" + (f"，之前读错{misread}次" if misread else "")
    return max(0.0, round(score - MISREAD_PENALTY * misread, 1)), reason


def score_entries(fact: dict, corrections: list[dict]) -> list[dict]:
    """Every scored question of the class; model-scored ones wait for the evaluation draft."""
    entries = []
    for question in questions(fact):
        basis = SCORING.get(question.purpose)
        if basis is None:
            continue
        if any(not c.get("task_id") or c["task_id"] == question.task_id for c in corrections):
            score, reason = None, CORRECTED
        elif basis == "rule":
            score, reason = rule_score(question)
        elif basis == "pronunciation":
            score, reason = pronunciation_score(question)
        elif not question.attempts:
            score, reason = None, NO_ANSWER
        elif not finished(question):
            score, reason = None, UNFINISHED
        else:
            score, reason = None, AWAITING
        entries.append(
            {
                "task_id": question.task_id,
                "task_instance_id": question.task_instance_id,
                "purpose": question.purpose,
                "dimension_id": question.dimension_id,
                "score": score,
                "basis": basis,
                "reason": reason,
                "limitations": [UNCALIBRATED] if basis == "pronunciation" else [],
            }
        )
    return entries


def model_score(given, wrong: bool, drafted: bool) -> tuple[int | None, str, str]:
    """(score, reason, check) for a question the evaluation model scores; check names a model
    score the rules overrode, empty when the score stands or none was given."""
    if given is None:
        return None, NO_MODEL_SCORE if drafted else EARLIER_DRAFT, ""
    if not 0 <= given.score <= 10:
        return None, "评价模型给分越界", "评价模型给分越界，按未知处理"
    if wrong and given.score == 10:
        return None, "有答错记录不能给10分", "有答错记录不能给10分，按未知处理"
    return given.score, given.reason, ""


def scoring_counts(entries: list[dict]) -> dict[str, int]:
    """For the log: how many questions by basis, and how many stayed unknown."""
    counts = {basis: 0 for basis in ("rule", "model", "pronunciation")}
    for entry in entries:
        counts[entry["basis"]] += 1
    return counts | {"unknown": sum(entry["score"] is None for entry in entries)}


# Scores in use (QT-4): averages read from stored evaluations only.
def average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def pooled(entries: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for entry in entries:
        groups.setdefault(entry[key], [])
        if entry["score"] is not None:
            groups[entry[key]].append(entry["score"])
    return {
        name: {"average": average(values), "count": len(values)} for name, values in groups.items()
    }


def lesson_scores(evaluation: dict) -> dict:
    """One lesson's averages by task type and by ability; unknown scores are only counted."""
    entries = evaluation.get("scores", [])
    return {
        "activity_id": evaluation["activity_id"],
        "course_ref": evaluation["alignment"]["tasks_materials"],
        "by_type": pooled(entries, "purpose"),
        "by_dimension": pooled(entries, "dimension_id"),
        "unknown": sum(entry["score"] is None for entry in entries),
    }


def recent_scores(evaluations: list[dict], lessons: int = 5) -> dict:
    """The last lessons (callers order them oldest first), pooled question by question."""
    latest = evaluations[-lessons:] if lessons else []
    entries = [entry for evaluation in latest for entry in evaluation.get("scores", [])]
    return {
        "lessons": len(latest),
        "by_type": pooled(entries, "purpose"),
        "by_dimension": pooled(entries, "dimension_id"),
    }
