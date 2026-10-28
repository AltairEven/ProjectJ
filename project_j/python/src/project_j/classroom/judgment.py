"""Classroom judgment as a provider-neutral request; the core keeps its own binding."""

from typing import Literal

from pydantic import Field

from project_j.contracts import Contract
from project_j.platform.models.contracts import ModelRequest
from project_j.platform.speech.contracts import PronunciationResult
from project_j.preparation.contracts import TaskDefinition

INSTRUCTIONS = (
    "你是Jane，一位AI学习老师，正在课堂中判断学生对当前任务的回应。"
    "只依据上下文里的任务、依据、参考答案与允许的帮助判断，不引入其他材料，不改变任务或状态。"
    "judgment取值：correct（与依据一致）、incorrect（与依据不一致）、unclear（无法确认意思）、"
    "help（core_decided_help为true时，按help_count选用allowed_supports，用完给exit_text）、"
    "reported（task_purpose为point或offscreen时，只记录已报告的条件）。"
    "学生一律用英文作答：answer整句或部分是中文（含英文句中夹中文词）时judgment为unclear，"
    "text提醒“Please answer in English.”，不算incorrect；"
    "point或offscreen的报告用中文时同样判unclear并提醒，不记为reported。英文换说法、小的拼写或语法错误，只要意思与依据一致就是correct。"
    "task_purpose为read_aloud时answer为空，judgment已由核心按发音评分定好，原样使用reading_judgment，不改判；"
    "flagged_words是评分标出的读错或漏读的词：correct时肯定学生，flagged_words不为空就请学生留意其中至多三个词；"
    "incorrect时点出其中至多三个词请学生再读；只提flagged_words里的词，不点评其他词；"
    "unclear时说明没有听清、不算读错，请学生再读或跳过；"
    "pronunciation.limitations写明评分限制（如门槛未校准）时，分数只作纠正提示，不向学生报分数或等级；"
    "error为inserted的条目表示多读或重复（word为音素），只提醒按原文读，不当作要重读的词；"
    "不另评发音，不改写评分。"
    "evidence必须原样使用上下文中的evidence；text是给学生的简短英文回应，只用英文，"
    "不夹任何中文字或中文标点，也不把题目或帮助翻译成中文。"
    "text按jane_method的规则与本学科策略组织，围绕goals；"
    "text是对学生说的话，用短句和学生能懂的常用词，尽量用本题prompt、evidence与allowed_supports里"
    "出现过的词，不出现evidence、reference answer这类说法。"
    "incorrect或unclear时引导学生回到原文再试，不直接说出reference_answers，"
    "也不说出evidence中能直接回答本题的词（可以使用allowed_supports里的说法）。"
    "correct时肯定学生，可以示范英语说法，但不再要求学生重说或改用英语再答，核心随后直接进入下一题。"
)
# Sent with open questions only: added to every request, these rules made the real model judge
# short unclear answers to other tasks as incorrect (QT-3 reference run, 13/18 against 18/18).
OPEN_INSTRUCTIONS = (
    "task_purpose为open时没有唯一答案：points是好回答应覆盖的要点，guidance说明怎样算够；"
    "回答与故事相符、覆盖要点或给出说得通的想法、用学生自己的英文说清楚为correct；"
    "与故事矛盾、离题、只有一两个词或缺少guidance要求的部分为incorrect，"
    "并在text里提示还缺什么，不替学生说出完整回答；无法确认意思为unclear；"
    "用中文作答按上面的规则判unclear并提醒用英文。"
)


# A reading passes with at most this share of its words flagged: one weak phone flags a word and
# the scorer is not calibrated, so a few flags are named, not failed (2026-10-28, ADR-0028).
FLAGGED_SHARE = 0.1


def reading_rule(pronunciation: dict | None) -> tuple[str, list[str]]:
    """The core's judgment of a reading from its score, and its flagged words in reading order."""
    if not pronunciation or pronunciation["status"] != "scored":
        return "unclear", []
    words = [word for word in pronunciation["words"] if word["error"] != "inserted"]
    flagged = [word["word"] for word in words if word["error"] in {"mispronounced", "omitted"}]
    judgment = "correct" if len(flagged) <= FLAGGED_SHARE * len(words) else "incorrect"
    return judgment, list(dict.fromkeys(word.strip(".,!?;:\"'") for word in flagged))


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


def method_context(jane: dict | None) -> dict | None:
    """The part of the bound Jane method a judgment needs."""
    return {k: jane[k] for k in ("rules", "strategies")} if jane else None


def judgment_request(
    task: TaskDefinition,
    text: str,
    help_count: int,
    is_help: bool,
    deadline_seconds: float,
    pronunciation: PronunciationResult | None = None,
    *,
    jane_method: dict | None = None,
    goals: tuple[str, ...] = (),
) -> ModelRequest:
    item = task.open if task.purpose == "open" else None
    score = pronunciation.model_dump(mode="json") if pronunciation else None
    return ModelRequest(
        purpose="classroom_judgment",
        instructions=INSTRUCTIONS + (OPEN_INSTRUCTIONS if item else ""),
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
            "pronunciation": score,
            "jane_method": jane_method,
            "goals": list(goals),
        }
        # A scored reading: the judgment the core fixed and the words to name.
        | (
            dict(zip(("reading_judgment", "flagged_words"), reading_rule(score), strict=True))
            if score
            else {}
        )
        # Open questions only: what a good answer covers and when it is enough.
        | (
            {"variant": item.variant, "points": list(item.points), "guidance": item.guidance}
            if item
            else {}
        ),
        output=ClassroomJudgment,
        deadline_seconds=deadline_seconds,
    )
