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

    def respond(self, request: ModelRequest) -> dict:
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
        return AdapterReply(
            data=self.respond(request),
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
