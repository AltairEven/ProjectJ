"""No SDK, microphone or speech output: bounded synthetic model/media results only."""

import asyncio
from typing import Literal

from pydantic import Field

from project_j.contracts import Contract
from project_j.preparation.contracts import TaskDefinition


class ModelProposal(Contract):
    activity_id: str
    task_instance_id: str
    wait_id: str
    core_revision: int
    cancellation_epoch: int
    event_id: str
    judgment: Literal["correct", "incorrect", "unclear", "help", "reported"]
    evidence: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_type: Literal["simulated"] = "simulated"


class SimulatedModel:
    def __init__(self):
        self.calls: list[dict] = []
        self.delay_seconds = 0.0
        self.fail_next = False

    async def judge(
        self, binding: dict, task: TaskDefinition, text: str, help_count: int, is_help: bool
    ):
        self.calls.append(
            {
                "purpose": "classroom",
                "model": "gpt-6.1-sol",
                "effort": "low",
                "evidence": "simulated",
                "event_id": binding["event_id"],
            }
        )
        await asyncio.sleep(self.delay_seconds)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated model failure")
        normalized = text.casefold().strip().rstrip(".!。！")
        if is_help:  # The core decides what is a help request; the model only words the support.
            judgment = "help"
            response = (
                task.supports[help_count].text
                if help_count < len(task.supports)
                else task.exit_text
            )
        elif task.purpose in {"point", "offscreen"}:
            judgment, response = (
                "reported",
                "我收到这段报告；只按已报告条件记录，未观察的部分保留未知。",
            )
        elif normalized in {
            item.casefold().strip().rstrip(".!。！") for item in task.acceptable_simulated
        }:
            judgment, response = "correct", "这与本题的文本依据对应。我们继续下一项。"
        elif normalized in {
            item.casefold().strip().rstrip(".!。！") for item in task.incorrect_simulated
        }:
            judgment, response = (
                "incorrect",
                "这个回答对应的对象或顺序与本题不一致。请回到题目的要求，再试一次。",
            )
        else:
            judgment, response = "unclear", "我还不能确认你的意思，请补充你指的对象或依据。"
        return ModelProposal(**binding, judgment=judgment, evidence=task.evidence, text=response)

    async def evaluate(self, activity_id: str, source_revision: int):
        self.calls.append(
            {
                "purpose": "evaluation",
                "model": "gpt-6.1-sol",
                "effort": "medium",
                "evidence": "simulated",
                "activity_id": activity_id,
                "source_revision": source_revision,
            }
        )
        await asyncio.sleep(self.delay_seconds)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated evaluation failure")
        return {"evidence": "simulated", "actual_api": "未验证"}
