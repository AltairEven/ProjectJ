from typing import Literal

from pydantic import Field

from project_j.classroom.judgment import method_context
from project_j.contracts import Contract, Document
from project_j.evaluation.scoring import AWAITING, CORRECTED, model_score, score_entries
from project_j.platform.models.contracts import ModelRequest
from project_j.preparation.task_types import CONTENT_TYPES


def interpret(
    fact: Document, feedbacks: list[Document], source_revision: int, purpose: str
) -> dict:
    corrections = [
        correction for feedback in feedbacks for correction in feedback.body.get("corrections", [])
    ]
    tasks = []
    for attempt in fact.body["attempts"]:
        affected = [
            correction
            for correction in corrections
            if not correction.get("task_id") or correction["task_id"] == attempt["task_id"]
        ]
        help_map = attempt["help"]
        judgment = (
            "needs_help"
            if any(value not in {"none", "unknown"} for value in help_map.values())
            else "unknown"
        )
        # Correctness of a task never fills unobserved independence, retention or transfer.
        tasks.append(
            {
                "task_id": attempt["task_id"],
                "attempt_id": attempt["attempt_id"],
                "task_instance_id": attempt["task_instance_id"],
                "dimension_id": attempt["dimension_id"],
                "task_judgment": "unclear" if affected else attempt["judgment"],
                "working_judgment": "unknown" if affected else judgment,
                "help": help_map,
                # Scoring-module evidence for read-aloud segments; the model never re-scores it.
                "pronunciation": attempt.get("pronunciation"),
                "external_help": "unknown",
                "retention": None,
                "transfer": None,
                "evidence_ref": fact.ref.model_dump(),
                "corrections": affected,
                "limitations": attempt["limitations"],
            }
        )
    return {
        "status": "checked",
        "activity_id": fact.body["activity_id"],
        "fact_ref": fact.ref.model_dump(),
        "feedback_refs": [fb.ref.model_dump() for fb in feedbacks],
        "source_revision": source_revision,
        "basis_version": "fixed-simulation-1",
        "purpose": purpose,
        "tasks": tasks,
        # Out of 10 per question (ADR-0020); model-scored ones are filled by apply_draft.
        "scores": score_entries(fact.body, corrections),
        "alignment": {
            "goals_scope": "Only what happened; tasks not done are not made up.",
            "tasks_materials": fact.body["course_ref"],
            "help_difficulty": "Help and task conditions are kept apart; outside help is unknown.",
            "process": fact.body["end"],
            "time_burden": "Only what happened; pace and workload are judged elsewhere.",
        },
        "limitations": [
            "Technical or simulated sources are not learning evidence.",
            "Recall, transfer and working alone were not observed.",
        ],
        "actual_api": "unverified",
        "actual_voice": "unverified",
    }


def next_suggestion(evaluation: Document, material: dict) -> dict:
    """material names the next fixed story; an already-read story is review, not new material."""
    title, focus = material["title"], material["focus"]
    if material["familiar"]:
        direction, change = (
            "Review a story read before and practise English frames",
            "Read-before and new material are sampled apart",
        )
        first = (
            f"Review {title}, read before: {focus}. Answers in a familiar story are not a first "
            "look at new material or transfer."
        )
    else:
        direction, change = (
            "Short observation with new material and English frames",
            "New material and real conditions are sampled apart",
        )
        first = f"Use {title}: {focus}."
    return {
        "status": "checked",
        "evaluation_ref": evaluation.ref.model_dump(),
        "direction": direction,
        "primary_change": change,
        "material": material,
        "sampling": [first, "First answers and tries after help are kept apart."],
        "help": ["Keep short English report frames."],
        "exit": "A challenge only replaces an activity; skip, pause and stop are allowed.",
        "review": "Recall and transfer need evidence from later dates and new conditions.",
        "limitations": ["A suggestion only; planning must adopt it before a lesson is made."],
    }


EVALUATION_INSTRUCTIONS = (
    "你是Jane的课后评价助手。只依据上下文里的封存事实、课程题目和学生补记，为学生整理本课；"
    "写给学生的summary、strongest、uncertain、alignment、explanation与scores的reason一律用简单英文，"
    "不夹中文字或中文标点，用词和句长不超过reading_level（学生的蓝思值；为null时按初学者写短句、常用词）；"
    "help_quote照原样逐字引用学生补记，不翻译。学生用中文作答的那次尝试按“没答”看待，不当作答错。"
    "不补造未观察的表现、保持或迁移，不重评发音，不改课堂判断，不给总分或等级；"
    "scores只给tasks里purpose为answer或open的题各一项（task_id）：看这题全部attempts的作答、judgment"
    "与帮助综合给0到10的整数分，有judgment为incorrect的不能给10分，reason用一句话说明。"
    "tasks里有content的choice、order、match、fill题由程序判定对错，open题没有唯一答案；"
    "attempts的answers是学生实际点选、填写或写下的内容。"
    "tasks必须覆盖attempts里的每个attempt_id，每个只出现一次。"
    "working_judgment：single_independent只用于初答judgment为correct、help里没有任何帮助、"
    "且学生补记明确说没有额外帮助的尝试；needs_help用于课中有帮助、学生补记了帮助，或答错的尝试；"
    "其余为unknown。“无额外补充”“Nothing to add”“没观察到”都不等于没有帮助。"
    "attempts的visuals是作答前学生页实际显示过的图片或动画：stage为question属于题目材料，"
    "为help属于帮助、随help说明；没列出的不能当作学生看到了。"
    "external_help：学生补记明确说没有别人帮忙为none，明确说有人帮了为reported，否则unknown；"
    "none或reported时help_quote必须逐字摘自feedback的text。"
    "explanation用一两句给学生说明这次尝试的依据和限制。summary概括实际完成与结束情况；"
    "strongest列最有依据的表现或帮助；uncertain列仍未确定的事项；"
    "alignment说明课程安排与实际课堂的关键差异及原因（如中途停止、未做的任务）。"
    "补记里写明是异议的，按异议重新核对相关题目，在explanation里回应。"
    "skill_focus是学生为本课勾选的加强项目：有时在summary里写学生在这些项目上的实际表现与依据，"
    "没有观察写未知，不另立评分。"
)


class TaskReading(Contract):
    attempt_id: str
    working_judgment: Literal["unknown", "needs_help", "single_independent"]
    external_help: Literal["none", "reported", "unknown"]
    help_quote: str = ""  # The parent's words, verbatim.
    explanation: str = Field(min_length=1)


class ScoreReading(Contract):
    task_id: str
    score: int
    reason: str = Field(min_length=1)


class EvaluationDraft(Contract):
    """A proposal only: apply_draft checks every judgment against the sealed facts."""

    tasks: tuple[TaskReading, ...]
    # Drafts saved before scoring have none; apply_draft tells them apart (ADR-0020).
    scores: tuple[ScoreReading, ...] = ()
    summary: str = Field(min_length=1)
    strongest: tuple[str, ...]
    uncertain: tuple[str, ...]
    alignment: str = Field(min_length=1)


def evaluation_request(
    fact: dict,
    course: dict,
    feedbacks: list[dict],
    jane: dict | None,
    level: str | None = None,
) -> ModelRequest:
    # Task data only: no learner identity, profile or full input signatures.
    end = fact["end"]
    return ModelRequest(
        purpose="evaluation",
        instructions=EVALUATION_INSTRUCTIONS,
        context={
            "end": {k: end.get(k) for k in ("reason", "unconfirmed", "unfinished_tasks")},
            "goals": [
                course["primary_goal"]["description"],
                course["auxiliary_goal"]["description"],
            ],
            "tasks": [
                {
                    "task_id": task["task_id"],
                    "purpose": task["purpose"],
                    "dimension_id": task["dimension_id"],
                    "prompt": task["prompt"],
                    "evidence": task["evidence"],
                    "reference_answers": task["acceptable_simulated"],
                    "supports": [support["text"] for support in task["supports"]],
                }
                | ({"content": task[task["purpose"]]} if task["purpose"] in CONTENT_TYPES else {})
                for task in course["tasks"]
            ],
            "attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "task_id": attempt["task_id"],
                    "dimension_id": attempt["dimension_id"],
                    # A clicked answer reads as what was picked or filled in.
                    "answers": [attempt["answer_text"]]
                    if "answer_text" in attempt
                    else [item.get("text") for item in attempt["inputs"]],
                    "judgment": attempt["judgment"],
                    "help": attempt["help"],
                    "visuals": attempt.get("visuals", []),
                    "pronunciation": attempt.get("pronunciation"),
                }
                for attempt in fact["attempts"]
            ],
            "feedback": [
                {"text": feedback["text"], "corrections": feedback["corrections"]}
                for feedback in feedbacks
            ],
            "jane_method": method_context(jane),
            # The student's reading level (their Lexile) for the English they read here.
            "reading_level": level,
            "limitations": fact["limitations"],
            # What the student ticked to train, as the course mapped it.
            "skill_focus": [
                item["requirement"]
                for item in course["demand_mapping"]
                if item["field"] in ("skill_focus", "skill_note")
            ],
        },
        output=EvaluationDraft,
        deadline_seconds=180,
    )


class DraftMismatch(ValueError):
    """The draft's attempts differ from the sealed facts; no part of it is usable."""


def apply_draft(base: dict, draft: EvaluationDraft, feedbacks: list[dict]) -> dict:
    """Overlay the model's proposal on interpret(); a judgment the rules forbid becomes unknown."""
    readings = {reading.attempt_id: reading for reading in draft.tasks}
    attempts = {task["attempt_id"] for task in base["tasks"]}
    if len(readings) != len(draft.tasks) or set(readings) != attempts:
        raise DraftMismatch("the draft does not match the tries in the class record")
    said = [feedback["text"] for feedback in feedbacks]
    tasks, checks = [], []
    for task in base["tasks"]:
        reading = readings[task["attempt_id"]]
        reasons = []
        external, quote = reading.external_help, reading.help_quote.strip()
        if external != "unknown" and not (quote and any(quote in text for text in said)):
            external = "unknown"
            reasons.append("Outside help was not quoted word for word from your notes; unknown")
        helped = any(level not in {"none", "unknown"} for level in task["help"].values())
        judgment = reading.working_judgment
        if task["corrections"]:
            if judgment != "unknown":
                reasons.append(CORRECTED)
            judgment = "unknown"
        elif judgment == "single_independent" and not (
            task["task_judgment"] == "correct"
            and all(level == "none" for level in task["help"].values())
            and external == "none"
        ):
            reasons.append("Not enough to call it done alone")
            judgment = "unknown"
        elif judgment == "needs_help" and not (
            helped or external == "reported" or task["task_judgment"] == "incorrect"
        ):
            reasons.append("No evidence that help was needed")
            judgment = "unknown"
        help_map = dict(task["help"])
        if judgment == "single_independent":
            help_map[task["dimension_id"]] = "none"  # learning requires it for independence
        tasks.append(
            task
            | {
                "working_judgment": judgment,
                "external_help": external,
                "help_quote": quote if external != "unknown" else "",
                "explanation": reading.explanation,
                "help": help_map,
                "limitations": [*task["limitations"], *reasons],
            }
        )
        checks += [
            {
                "attempt_id": task["attempt_id"],
                "task_id": task["task_id"],
                "proposed": reading.working_judgment,
                "applied": judgment,
                "reason": reason,
            }
            for reason in reasons
        ]
    scores, score_checks = applied_scores(base, draft)
    return base | {
        "tasks": tasks,
        "scores": scores,
        "checks": checks + score_checks,
        "report": {
            "summary": draft.summary,
            "strongest": list(draft.strongest),
            "uncertain": list(draft.uncertain),
            "alignment": draft.alignment,
        },
        "draft": draft.model_dump(mode="json"),
        "basis_version": "evaluation-draft-1",
    }


def applied_scores(base: dict, draft: EvaluationDraft) -> tuple[list[dict], list[dict]]:
    """The model's scores where the rules accept them; anything else stays unknown."""
    given: dict[str, ScoreReading] = {}
    for reading in draft.scores:
        given.setdefault(reading.task_id, reading)
    waiting = {
        entry["task_id"]
        for entry in base.get("scores", [])
        if entry["basis"] == "model" and entry["reason"] == AWAITING
    }
    drafted = "scores" in draft.model_fields_set
    scores, checks = [], []
    for entry in base.get("scores", []):
        if entry["task_id"] not in waiting:
            scores.append(entry)
            continue
        wrong = any(
            task["task_judgment"] == "incorrect"
            for task in base["tasks"]
            if task["task_instance_id"] == entry["task_instance_id"]
        )
        reading = given.get(entry["task_id"])
        score, reason, check = model_score(reading, wrong, drafted)
        scores.append(entry | {"score": score, "reason": reason})
        if check:
            checks.append(
                {
                    "task_id": entry["task_id"],
                    "proposed": reading.score if reading else None,
                    "applied": score,
                    "reason": check,
                }
            )
    checks += [
        {
            "task_id": task_id,
            "proposed": reading.score,
            "applied": None,
            "reason": "Not a scored question",
        }
        for task_id, reading in given.items()
        if task_id not in waiting
    ]
    return scores, checks


FAILURE_TEXT = {
    "timeout": "it took too long",
    "invalid_output": "the reply was incomplete",
    "unavailable": "the model is not available now",
    "not_initialized": "the model is not set up",
    "config_mismatch": "the model setup does not match",
    "language": "the reply was not in English",
}


class ModelStepUnavailable(Exception):
    """A model step saved nothing; what it would have changed stays and the request can retry.
    Each step keeps its own subclass so logs and handlers can tell them apart."""

    def __init__(self, reason: str, subject: str, kept: str):
        text = FAILURE_TEXT.get(reason, reason)
        super().__init__(f"The {subject} could not be made ({text}). {kept}; you can try again.")


class EvaluationUnavailable(ModelStepUnavailable):
    """No evaluation was saved; the sealed facts and feedback stay and the request can retry."""

    def __init__(self, reason: str):
        super().__init__(reason, "evaluation", "The class record and your notes are saved")
