from typing import Literal

from pydantic import Field

from project_j.classroom.judgment import method_context
from project_j.contracts import Contract, Document
from project_j.platform.models.contracts import ModelRequest


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
        "alignment": {
            "goals_scope": "按实际已发生范围，未做任务不补造。",
            "tasks_materials": fact.body["course_ref"],
            "help_difficulty": "帮助与任务条件分维度，外部帮助未知。",
            "process": fact.body["end"],
            "time_burden": "模拟流程；实际30分钟与家庭负担未验证。",
        },
        "limitations": ["成人/技术/模拟不能转为真实儿童学习证据。", "保持、迁移与独立条件未观察。"],
        "actual_api": "未验证",
        "actual_voice": "未验证",
    }


def next_suggestion(evaluation: Document, material: dict) -> dict:
    """material names the next fixed story; an already-read story is review, not new material."""
    title, focus = material["title"], material["focus"]
    if material["familiar"]:
        direction, change = "已读材料复习及英语支架巩固", "已读材料与首次新材料分开取样"
        first = (
            f"复习已读的{title}，观察{focus}的文本依据；熟悉条件下的回答不作首次新材料观察或迁移。"
        )
    else:
        direction, change = "新材料短观察及英语支架巩固", "新材料与实际条件分别取样"
        first = f"用{title}观察{focus}的文本依据。"
    return {
        "status": "checked",
        "evaluation_ref": evaluation.ref.model_dump(),
        "direction": direction,
        "primary_change": change,
        "material": material,
        "sampling": [first, "初答与帮助后再试分开。"],
        "help": ["保留英语短报告句框，科学条件可先用中文报告。"],
        "exit": "原30分钟预算内，挑战替换；允许跳过、暂停、停止。",
        "review": "保持与迁移需要后续新日期和新条件证据。",
        "limitations": ["候选建议，须learning采用并保存需求后才能备课。"],
    }


EVALUATION_INSTRUCTIONS = (
    "你是Jane的课后评价助手。只依据上下文里的封存事实、课程题目和家长反馈，为家长整理本课；"
    "不补造未观察的表现、保持或迁移，不重评发音，不改课堂判断，不给总分或等级。"
    "tasks必须覆盖attempts里的每个attempt_id，每个只出现一次。"
    "working_judgment：single_independent只用于初答judgment为correct、help里没有任何帮助、"
    "且家长反馈明确说没有额外帮助的尝试；needs_help用于课中有帮助、家长报告了帮助，或答错的尝试；"
    "其余为unknown。“无额外补充”“没观察到”都不等于没有帮助。"
    "external_help：家长反馈明确说没有帮忙为none，明确说帮了或代答为reported，否则unknown；"
    "none或reported时help_quote必须逐字摘自feedback的text。"
    "explanation用一两句给家长说明这次尝试的依据和限制。summary概括实际完成与结束情况；"
    "strongest列最有依据的表现或帮助；uncertain列仍未确定的事项；"
    "alignment说明课程安排与实际课堂的关键差异及原因（如中途停止、未做的任务）。"
    "limitations写明成人或模拟来源时，在summary中说明不能作为儿童学习证据。"
)


class TaskReading(Contract):
    attempt_id: str
    working_judgment: Literal["unknown", "needs_help", "single_independent"]
    external_help: Literal["none", "reported", "unknown"]
    help_quote: str = ""  # The parent's words, verbatim.
    explanation: str = Field(min_length=1)


class EvaluationDraft(Contract):
    """A proposal only: apply_draft checks every judgment against the sealed facts."""

    tasks: tuple[TaskReading, ...]
    summary: str = Field(min_length=1)
    strongest: tuple[str, ...]
    uncertain: tuple[str, ...]
    alignment: str = Field(min_length=1)


def evaluation_request(
    fact: dict, course: dict, feedbacks: list[dict], jane: dict | None
) -> ModelRequest:
    # Task data only: no learner identity, household or full input signatures.
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
                for task in course["tasks"]
            ],
            "attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "task_id": attempt["task_id"],
                    "dimension_id": attempt["dimension_id"],
                    "answers": [item.get("text") for item in attempt["inputs"]],
                    "judgment": attempt["judgment"],
                    "help": attempt["help"],
                    "pronunciation": attempt.get("pronunciation"),
                }
                for attempt in fact["attempts"]
            ],
            "feedback": [
                {"text": feedback["text"], "corrections": feedback["corrections"]}
                for feedback in feedbacks
            ],
            "jane_method": method_context(jane),
            "limitations": fact["limitations"],
        },
        output=EvaluationDraft,
        deadline_seconds=60,
    )


class DraftMismatch(ValueError):
    """The draft's attempts differ from the sealed facts; no part of it is usable."""


def apply_draft(base: dict, draft: EvaluationDraft, feedbacks: list[dict]) -> dict:
    """Overlay the model's proposal on interpret(); a judgment the rules forbid becomes unknown."""
    readings = {reading.attempt_id: reading for reading in draft.tasks}
    attempts = {task["attempt_id"] for task in base["tasks"]}
    if len(readings) != len(draft.tasks) or set(readings) != attempts:
        raise DraftMismatch("评价草稿与封存事实的尝试不一致")
    said = [feedback["text"] for feedback in feedbacks]
    tasks, checks = [], []
    for task in base["tasks"]:
        reading = readings[task["attempt_id"]]
        reasons = []
        external, quote = reading.external_help, reading.help_quote.strip()
        if external != "unknown" and not (quote and any(quote in text for text in said)):
            external = "unknown"
            reasons.append("家外帮助未逐字引用家长反馈，按未知处理")
        helped = any(level not in {"none", "unknown"} for level in task["help"].values())
        judgment = reading.working_judgment
        if task["corrections"]:
            if judgment != "unknown":
                reasons.append("受家长更正影响，待复核")
            judgment = "unknown"
        elif judgment == "single_independent" and not (
            task["task_judgment"] == "correct"
            and all(level == "none" for level in task["help"].values())
            and external == "none"
        ):
            reasons.append("单次独立的条件不足")
            judgment = "unknown"
        elif judgment == "needs_help" and not (
            helped or external == "reported" or task["task_judgment"] == "incorrect"
        ):
            reasons.append("需要帮助缺少依据")
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
    return base | {
        "tasks": tasks,
        "checks": checks,
        "report": {
            "summary": draft.summary,
            "strongest": list(draft.strongest),
            "uncertain": list(draft.uncertain),
            "alignment": draft.alignment,
        },
        "draft": draft.model_dump(mode="json"),
        "basis_version": "evaluation-draft-1",
    }


FAILURE_TEXT = {
    "timeout": "超时",
    "invalid_output": "结构不完整",
    "unavailable": "模型暂不可用",
    "not_initialized": "模型未初始化",
    "config_mismatch": "实际配置不符",
}


class EvaluationUnavailable(Exception):
    """No evaluation was saved; the sealed facts and feedback stay and the request can retry."""

    def __init__(self, reason: str):
        text = FAILURE_TEXT.get(reason, reason)
        super().__init__(f"课后评价未取得（{text}）。事实与反馈已保存，可以重试。")
