"""Classroom judgment as a provider-neutral request; the core keeps its own binding."""

from typing import Literal

from pydantic import Field

from project_j.contracts import Contract
from project_j.platform.models.contracts import ModelRequest
from project_j.platform.speech.contracts import PronunciationResult
from project_j.preparation.contracts import TaskDefinition

INSTRUCTIONS = (
    "你是Jane，一位AI学习老师，正在课堂中判断孩子对当前任务的回应。"
    "只依据上下文里的任务、依据、参考答案与允许的帮助判断，不引入其他材料，不改变任务或状态。"
    "judgment取值：correct（与依据一致）、incorrect（与依据不一致）、unclear（无法确认意思）、"
    "help（core_decided_help为true时，按help_count选用allowed_supports，用完给exit_text）、"
    "reported（task_purpose为point或offscreen时，只记录已报告的条件）。"
    "task_purpose为read_aloud时answer为空：只依据pronunciation（发音评分模块的结果）与reference_text判断，"
    "达到要求为correct，需要纠正的词为incorrect并在text中点出这些词，pronunciation.status为unknown时为unclear；"
    "不另评发音，不改写评分。"
    "evidence必须原样使用上下文中的evidence；text是给孩子的简短中文回应。"
)


class ClassroomJudgment(Contract):
    judgment: Literal["correct", "incorrect", "unclear", "help", "reported"]
    evidence: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ModelProposal(ClassroomJudgment):
    """A model judgment bound by the core; the binding never passes through the model."""

    activity_id: str
    task_instance_id: str
    wait_id: str
    core_revision: int
    cancellation_epoch: int
    event_id: str
    call_id: str


def judgment_request(
    task: TaskDefinition,
    text: str,
    help_count: int,
    is_help: bool,
    deadline_seconds: float,
    pronunciation: PronunciationResult | None = None,
) -> ModelRequest:
    return ModelRequest(
        purpose="classroom_judgment",
        instructions=INSTRUCTIONS,
        context={
            "task_purpose": task.purpose,
            "prompt": task.prompt,
            "evidence": task.evidence,
            "reference_answers": list(task.acceptable_simulated),
            "known_incorrect": list(task.incorrect_simulated),
            "allowed_supports": [support.text for support in task.supports],
            "exit_text": task.exit_text,
            "help_count": help_count,
            "core_decided_help": is_help,
            "answer": text,
            "reference_text": task.reference_text,
            "pronunciation": pronunciation.model_dump(mode="json") if pronunciation else None,
        },
        output=ClassroomJudgment,
        deadline_seconds=deadline_seconds,
    )
