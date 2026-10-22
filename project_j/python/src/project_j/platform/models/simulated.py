"""Simulated adapter: deterministic replies from the request context; no SDK or network."""

import asyncio
from datetime import UTC, datetime

from project_j.english import ENGLISH_ONLY, chinese, han
from project_j.platform.models.contracts import (
    AdapterReply,
    Binding,
    CapabilityReport,
    ConfigCheck,
    Effort,
    ModelRequest,
    ProbeItem,
    Tier,
    solid_png,
)

SIMULATED_MODEL = "simulated-jane"
SIMULATED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20">'
    '<animate attributeName="r" values="20;35;20" dur="2s" repeatCount="indefinite"/>'
    "</circle></svg>"
)


def normalized(text: str) -> str:
    return text.casefold().strip().rstrip(".!。！")


def english(answer: str) -> str:
    """A simulated profile keeps an English chat answer; it cannot translate a Chinese one."""
    return "Simulated: written in English from your chat answer." if chinese(answer) else answer


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
            # Story and open questions: 10 when every try was right without help, else 6.
            scored = {t["task_id"] for t in context["tasks"] if t["purpose"] in {"answer", "open"}}
            tries: dict[str, list[dict]] = {}
            for attempt in context["attempts"]:
                if attempt["task_id"] in scored:
                    tries.setdefault(attempt["task_id"], []).append(attempt)
            return {
                "tasks": [
                    {
                        "attempt_id": attempt["attempt_id"],
                        "working_judgment": "needs_help"
                        if any(v not in {"none", "unknown"} for v in attempt["help"].values())
                        else "unknown",
                        "external_help": "unknown",
                        "explanation": "Simulated evaluation from the class record.",
                    }
                    for attempt in context["attempts"]
                ],
                "scores": [
                    {
                        "task_id": task_id,
                        "score": 10
                        if all(
                            a["judgment"] == "correct"
                            and all(v in {"none", "unknown"} for v in a["help"].values())
                            for a in attempts
                        )
                        else 6,
                        "reason": "Simulated score",
                    }
                    for task_id, attempts in tries.items()
                ],
                "summary": "Simulated evaluation of the class record; no real model was called.",
                "strongest": [],
                "uncertain": ["Working alone, recall and transfer were not observed."],
                "alignment": "Only what happened.",
            }
        if request.purpose == "preparation":
            if context.get("kind") == "animation":
                return {
                    "svg": SIMULATED_SVG,
                    "description": "Simulated animation: a circle grows and shrinks.",
                }
            if "feedback" in context:  # A discussion round keeps the course unchanged.
                note = (
                    "Simulated: your feedback is noted; no real model was called; the lesson "
                    "is unchanged."
                )
                return context["previous"] | {"note": note}
            # The simulated model only reproduces the fixed library template.
            return context["template"]
        if request.purpose == "planning" and "chat" in context:
            return {
                "background": "Simulated summary of why you learn English and how you use it.",
                "interests": english(context["chat"][1]["answer"]),
                "weekly_time": english(context["chat"][2]["answer"]),
                "focus": english(context["chat"][3]["answer"]),
                "note": "Simulated: the profile picks story level and topics.",
            }
        if request.purpose == "planning" and "demand" in context:
            from project_j.learning.fixtures import AUXILIARY_GOAL, PRIMARY_GOAL

            demand, suggestion = context["demand"], context["suggestion"]
            # The simulated course covers only the fixed goals, so after a placement lesson
            # (other goals) the simulated planner moves there unless told to keep the goals.
            fixed = (PRIMARY_GOAL.model_dump(), AUXILIARY_GOAL.model_dump())
            goals = (demand["primary_goal"], demand["auxiliary_goal"])
            if "keep_goals" not in context:
                goals = fixed
            return {
                "suggestion_action": "adopt",
                "suggestion_reason": "Simulated plan: the suggestion fits the goals and the time.",
                "direction": "consolidate",
                "primary_goal": goals[0],
                "auxiliary_goal": goals[1],
                "prerequisites": demand["prerequisites"],
                "sampling_requirements": suggestion["sampling"],
                "help_requirements": suggestion["help"],
                "offscreen_requirements": demand["offscreen_requirements"],
                "fallback": demand["fallback"],
                "gaps": ["Recall, transfer, outside help and real voice are still to observe."],
                "plan_effect": "reused",
                "plan_reason": "The week plan stays: goals and time did not need to change.",
                "plan_patch": None,
                "goal_reason": "Simulated plan: the two goals stay.",
                # The simulated preparation only reproduces the library, so it offers that story.
                "topic_suggestions": [
                    {
                        "title": suggestion["material"]["title"],
                        "reason": "Simulated: the next library lesson.",
                    }
                ],
                "note": {
                    "before": "The current plan and week plan.",
                    "after": "Simulated plan: goals kept, suggestion adopted.",
                    "why": "From the saved evaluation and suggestion; no real model was called.",
                    "uncertain": ["Working alone, recall and transfer were not observed."],
                    "expectation": suggestion["sampling"][0],
                },
            }
        if request.purpose == "planning":
            return {
                "conclusion": "Only what exists is summed up; hours do not mean done or mastered.",
                "unknown": ["Recall, transfer and workload are still unknown."],
                "next_review_condition": "Review again after new dates, materials or important "
                "conditions.",
                "plan_effect": "reused",
                "patch": None,
                "reason": "Simulated review: not enough evidence to change.",
                "impact": "The current plan stays.",
                "alternatives": ["Keep the current plan"],
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
                    "text": "No score came through. Please read this part again.",
                }
            weak = [word["word"] for word in score["words"] if word["error"] != "none"]
            if not weak:
                return {
                    "judgment": "correct",
                    "evidence": evidence,
                    "text": "Clear and complete. Let's go on.",
                }
            return {
                "judgment": "incorrect",
                "evidence": evidence,
                "text": "Read these words again: " + ", ".join(weak[:5]),
            }
        if han(context["answer"]):  # The student answers in English only (ADR-0021).
            return {"judgment": "unclear", "evidence": evidence, "text": ENGLISH_ONLY}
        answer = normalized(context["answer"])
        if context["task_purpose"] == "open":  # No single answer: three words or more will do.
            if len(context["answer"].split()) >= 3:
                return {
                    "judgment": "correct",
                    "evidence": evidence,
                    "text": "Clearly said. Let's go on.",
                }
            return {
                "judgment": "unclear",
                "evidence": evidence,
                "text": "Say a little more: " + context["guidance"],
            }
        if context["task_purpose"] in {"point", "offscreen"}:
            judgment = "reported"
            text = (
                "Thanks for the report. Only what you reported is recorded; anything not "
                "seen stays unknown."
            )
        elif answer in {normalized(item) for item in context["reference_answers"]}:
            judgment, text = "correct", "That matches the words in the story. Let's go on."
        elif answer in {normalized(item) for item in context["known_incorrect"]}:
            judgment = "incorrect"
            text = (
                "That answer points to a different thing or order. Read the question again "
                "and try once more."
            )
        else:
            judgment, text = (
                "unclear",
                "I can't tell what you mean yet. Please say which thing or which words you mean.",
            )
        return {"judgment": judgment, "evidence": evidence, "text": text}

    async def invoke(self, request: ModelRequest, binding: Binding) -> AdapterReply:
        await asyncio.sleep(self.delay_seconds)
        if self.fail_next:
            self.fail_next = False
            return AdapterReply(data=None, failure="unavailable")
        media = ()
        if request.media == "image":
            data = {"description": "Simulated picture: one plain square; no real model was called."}
            media = (solid_png(),)
        else:
            data = self.respond(request)
        if data is None:  # Nothing the simulated model can reproduce.
            return AdapterReply(data=None, failure="unavailable")
        return AdapterReply(
            data=data,
            media=media,
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
        verified = ProbeItem(status="verified", detail="simulated")
        items = {name: verified for name in ("auth", "model", "structured_output", "cancellation")}
        items |= {f"effort:{effort}": verified for effort in efforts}
        items["image_input"] = ProbeItem(
            status="unsupported", detail="the simulated adapter takes no pictures"
        )
        items["audio"] = ProbeItem(status="not_probed", detail="voice is designed separately")
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
