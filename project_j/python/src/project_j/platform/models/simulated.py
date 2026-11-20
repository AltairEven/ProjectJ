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
        # Picture checks to fail, one set per check: the 1-based pictures judged wrong.
        self.reject_pictures: list[set[int]] = []

    async def listed(self) -> list[str]:
        return [SIMULATED_MODEL]

    def respond(self, request: ModelRequest) -> dict | None:
        context = request.context
        if request.purpose == "evaluation":
            from project_j.evaluation.assessment import working_judgment

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
                        "working_judgment": working_judgment(
                            attempt["judgment"], attempt["help"], False, "unknown"
                        ),
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
                "uncertain": ["Recall and transfer were not observed."],
                "alignment": "Only what happened.",
            }
        if request.purpose == "vocabulary":
            return {
                "words": [
                    {
                        "word": item["word"],
                        "lemma": item["word"].lower(),
                        "part_of_speech": "noun",
                        "definition": "Simulated meaning of this word in the story.",
                    }
                    for item in context["words"]
                ]
            }
        if request.purpose == "reading":
            return reading_reply(context)
        if request.purpose == "preparation":
            if context.get("kind") == "check":
                failing = self.reject_pictures.pop(0) if self.reject_pictures else set()
                return {
                    "pictures": [
                        {
                            "picture": number,
                            "ok": number not in failing,
                            "reason": "wrong" if number in failing else None,
                        }
                        for number in range(1, len(request.images) + 1)
                    ]
                }
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
                # Like a real plan, the simulated one follows the difficulty rule (ADR-0030).
                "direction": {"up": "advance", "down": "review"}.get(
                    context["difficulty"]["step"], "consolidate"
                ),
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
                        "form": "story",
                    }
                ],
                "note": {
                    "before": "The current plan and week plan.",
                    "after": "Simulated plan: goals kept, suggestion adopted.",
                    "why": "From the saved evaluation and suggestion; no real model was called.",
                    "uncertain": ["Recall and transfer were not observed."],
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
        if context["task_purpose"] == "read_aloud":  # The core fixed the judgment; only words.
            judgment, named = context["reading_judgment"], ", ".join(context["flagged_words"][:3])
            text = {
                "unclear": "No score came through. Please read this part again.",
                "incorrect": f"Read these words again: {named}",
                "correct": f"Good reading. Watch these words: {named}"
                if named
                else "Clear and complete. Let's go on.",
            }[judgment]
            return {"judgment": judgment, "evidence": evidence, "text": text}
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
        items["image_input"] = ProbeItem(status="verified", detail="simulated")
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


def reading_reply(context: dict) -> dict:
    """A fixed book that passes the checks: four chapters, the words dealt out in turn and every
    original picture in chapter 1; a chapter long enough for its range, using its words, with
    original pictures from paragraph 1 on and the new one last. A revision reads differently."""
    if context["kind"] == "outline":
        chapters = [
            {"title": f"Part {n}", "plot": "The story goes on.", "words": []} for n in range(4)
        ]
        for index, word in enumerate(context["words"]):
            chapters[index % 4]["words"].append(word["lemma"])
        return {
            "title": "The Longer Story",
            "characters": ["Mia: a small girl in a red coat."],
            "style": context["style"] or "Soft watercolor, simple shapes.",
            "chapters": chapters,
            "pictures": [
                {"picture": item["picture"], "chapter": 1} for item in context["pictures"]
            ],
        }
    line = (
        "Mia stopped by the window and listened to the rain."  # 10 words
        if context["kind"] == "revision"
        else "Mia walked on and looked at the hills again."  # 9 words
    )
    low = context["range"][0]
    words = " ".join(f"She thought about the {word}." for word in context["words"])
    paragraphs, count = [words] if words else [], 5 * len(context["words"])
    while count < low or len(paragraphs) <= len(context["pictures"]):
        paragraphs.append(" ".join([line] * 5))
        count += 5 * len(line.split())
    return {
        "title": f"Chapter {context['number']}"
        + (", Again" if context["kind"] == "revision" else ""),
        "paragraphs": paragraphs,
        "pictures": [
            {"picture": item["picture"], "paragraph": n}
            for n, item in enumerate(context["pictures"], start=1)
        ],
        "new_picture_paragraph": len(paragraphs),
        "new_picture": "Mia by the window in the rain.",
    }
