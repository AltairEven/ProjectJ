"""Simulated adapter: deterministic replies from the request context; no SDK or network."""

import asyncio
from datetime import UTC, datetime

from project_j.platform.models.contracts import (
    AdapterReply,
    Binding,
    CapabilityReport,
    ConfigCheck,
    Effort,
    ModelRequest,
    ProbeItem,
    Tier,
)

SIMULATED_MODEL = "simulated-jane"


def normalized(text: str) -> str:
    return text.casefold().strip().rstrip(".!。！")


class SimulatedAdapter:
    adapter_id = "simulated"
    version = "1"
    efforts: tuple[Effort, ...] = ("low", "medium", "high")
    tiers: tuple[Tier, ...] = ("default",)
    simulated = True

    def __init__(self):
        self.delay_seconds = 0.0
        self.fail_next = False

    def respond(self, request: ModelRequest) -> dict | None:
        context = request.context
        if request.purpose == "evaluation":
            return {
                "tasks": [
                    {
                        "attempt_id": attempt["attempt_id"],
                        "working_judgment": "needs_help"
                        if any(v not in {"none", "unknown"} for v in attempt["help"].values())
                        else "unknown",
                        "external_help": "unknown",
                        "explanation": "模拟评价：按封存事实整理。",
                    }
                    for attempt in context["attempts"]
                ],
                "summary": "模拟评价：按封存事实与反馈整理，未调用真实模型。",
                "strongest": [],
                "uncertain": ["独立条件、保持与迁移未观察。"],
                "alignment": "按实际已发生范围整理。",
            }
        if request.purpose == "preparation":
            if "feedback" in context:  # A discussion round keeps the course unchanged.
                note = "模拟：已记录意见，未调用真实模型，课程内容不变。"
                return context["previous"] | {"note": note}
            # The simulated model only reproduces the fixed library template.
            return context["template"]
        if request.purpose == "planning" and "demand" in context:
            demand, suggestion = context["demand"], context["suggestion"]
            return {
                "suggestion_action": "adopt",
                "suggestion_reason": "模拟规划：在既定目标与30分钟预算内采用候选建议。",
                "direction": "consolidate",
                "primary_goal": demand["primary_goal"],
                "auxiliary_goal": demand["auxiliary_goal"],
                "prerequisites": demand["prerequisites"],
                "sampling_requirements": suggestion["sampling"],
                "help_requirements": suggestion["help"],
                "offscreen_requirements": demand["offscreen_requirements"],
                "fallback": demand["fallback"],
                "gaps": ["保持、迁移、外部帮助与真实声音条件仍待观察。"],
                "plan_effect": "reused",
                "plan_reason": "沿用当前周计划：既定目标与预算未发生必要变化。",
                "plan_patch": None,
                "note": {
                    "before": "当前需求与周计划。",
                    "after": "模拟规划：沿用目标并采纳候选建议。",
                    "why": "按已保存评价与候选建议，未调用真实模型。",
                    "uncertain": ["独立条件、保持与迁移未观察。"],
                    "expectation": suggestion["sampling"][0],
                },
            }
        if request.purpose == "planning":
            return {
                "conclusion": "只汇总已有范围；日期和课时不产生完成或掌握。",
                "unknown": ["保持、迁移及真实家庭负担仍未知。"],
                "next_review_condition": "取得新日期、材料或重要家庭条件后再复核。",
                "plan_effect": "reused",
                "patch": None,
                "reason": "模拟复盘：依据不足以调整。",
                "impact": "保留既有有效安排。",
                "alternatives": ["沿用原安排"],
            }
        evidence = context["evidence"]
        if context["core_decided_help"]:  # The core decides help; the model only words it.
            supports, count = context["allowed_supports"], context["help_count"]
            text = supports[count] if count < len(supports) else context["exit_text"]
            return {"judgment": "help", "evidence": evidence, "text": text}
        if context["task_purpose"] == "read_aloud":
            score = context["pronunciation"]
            if score["status"] != "scored":
                return {
                    "judgment": "unclear",
                    "evidence": evidence,
                    "text": "这次没有取得评分，请再读一遍这一段。",
                }
            weak = [word["word"] for word in score["words"] if word["error"] != "none"]
            if not weak:
                return {
                    "judgment": "correct",
                    "evidence": evidence,
                    "text": "读得清楚完整，我们继续。",
                }
            return {
                "judgment": "incorrect",
                "evidence": evidence,
                "text": "这几个词再读一遍：" + "、".join(weak[:5]),
            }
        answer = normalized(context["answer"])
        if context["task_purpose"] in {"point", "offscreen"}:
            judgment = "reported"
            text = "我收到这段报告；只按已报告条件记录，未观察的部分保留未知。"
        elif answer in {normalized(item) for item in context["reference_answers"]}:
            judgment, text = "correct", "这与本题的文本依据对应。我们继续下一项。"
        elif answer in {normalized(item) for item in context["known_incorrect"]}:
            judgment = "incorrect"
            text = "这个回答对应的对象或顺序与本题不一致。请回到题目的要求，再试一次。"
        else:
            judgment, text = "unclear", "我还不能确认你的意思，请补充你指的对象或依据。"
        return {"judgment": judgment, "evidence": evidence, "text": text}

    async def invoke(self, request: ModelRequest, binding: Binding) -> AdapterReply:
        await asyncio.sleep(self.delay_seconds)
        if self.fail_next:
            self.fail_next = False
            return AdapterReply(data=None, failure="unavailable")
        data = self.respond(request)
        if data is None:  # Nothing the simulated model can reproduce.
            return AdapterReply(data=None, failure="unavailable")
        return AdapterReply(
            data=data,
            actual={
                "model": ConfigCheck(
                    requested=binding.model, actual=binding.model, status="verified"
                ),
                "effort": ConfigCheck(
                    requested=binding.effort, actual=binding.effort, status="verified"
                ),
            },
        )

    def report(self, model: str, efforts: tuple[Effort, ...]) -> CapabilityReport:
        verified = ProbeItem(status="verified", detail="模拟")
        items = {name: verified for name in ("auth", "model", "structured_output", "cancellation")}
        items |= {f"effort:{effort}": verified for effort in efforts}
        items["image_input"] = ProbeItem(status="unsupported", detail="模拟适配器不处理图片")
        items["audio"] = ProbeItem(status="not_probed", detail="语音另行设计")
        return CapabilityReport(
            adapter=self.adapter_id,
            adapter_version=self.version,
            model=model,
            simulated=True,
            captured_at=datetime.now(UTC).isoformat(),
            items=items,
        )

    async def probe(
        self, model: str, efforts: tuple[Effort, ...], tiers: tuple[Tier, ...] = ()
    ) -> CapabilityReport:
        return self.report(model, efforts)
