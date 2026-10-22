"""Onboarding (ADR-0018): Lexile, a short chat with Jane, a placement lesson, the profile.

The chat is the student's own account, kept word for word and never scored. The placement
lesson is an ordinary prepared lesson; its evaluation is accepted with the profile.
"""

import logging
import time

from project_j.application.documents import document, identity, publication
from project_j.contracts import Contract, Document, RevisionRef
from project_j.english import chinese
from project_j.learning.contracts import (
    Challenge,
    ChatTurn,
    DemandBody,
    Goal,
    ProfileBody,
    Text,
    normalize_lexile,
)
from project_j.learning.planning import PlanningUnavailable, evaluation_context
from project_j.platform.models.contracts import ModelRequest
from project_j.platform.storage import Conflict

log = logging.getLogger(__name__)

QUESTIONS = (
    "Why do you learn English?",
    "What do you like to do?",
    "How much time can you study each week?",
    "What do you want to do better: reading, speaking, listening or saying words?",
)

PROFILE_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的AI教师，不是学生；学习者不具名，一律称“学生”。"
    "根据chat里学生对Jane固定问题的原话、lexile与evaluation（建档小测的评价），整理学习资料："
    "background写学习目的与当前英语使用情况，interests写兴趣，weekly_time写每周可用时间，"
    "focus写最想加强的方面。只写原话与评价支持的内容，不推测年龄、身份或能力等级，"
    "原话没说的写“Not said”。note用一句话告诉学生这份资料怎样用于选课。"
    "学生的原话可能是中文或英文；写出的background、interests、weekly_time、focus和note"
    "一律用简单英文，不夹中文字或中文标点，用词和句长不超过lexile（学生的蓝思值）。"
)


class ProfileDraft(Contract):
    background: Text
    interests: Text
    weekly_time: Text
    focus: Text
    note: Text


class ProfileEdit(Contract):
    """What the student confirms or changes; Lexile and usable things are optional here."""

    background: Text
    interests: Text
    weekly_time: Text
    focus: Text
    materials: Text | None = None
    lexile: Text | None = None
    challenge: Challenge | None = None  # None keeps the saved step.


def profile(app, learner_id: str) -> Document | None:
    return app.store.current(identity(learner_id, "profile"), learner_id)


def revised(operation: str, learner_id: str, prior: Document | None, body: ProfileBody):
    return document(
        "profile",
        identity(learner_id, "profile"),
        operation,
        learner_id,
        body,
        revision=prior.revision + 1 if prior else 1,
    )


def save_lexile(app, request_id: str, learner_id: str, raw: str) -> dict:
    lexile = normalize_lexile(raw)
    signature = {"lexile": lexile}
    completed = app.ledger.completed_request(request_id, learner_id, "lexile", signature)
    if completed:
        return completed
    prior = profile(app, learner_id)
    operation = app.request(
        request_id, learner_id, "lexile", signature, baselines=(prior.ref,) if prior else ()
    )
    saved = app.saved_step(operation, learner_id, "profile")
    if saved is None:
        base = ProfileBody.model_validate(prior.body) if prior else ProfileBody()
        obj = revised(operation, learner_id, prior, base.model_copy(update={"lexile": lexile}))
        saved = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "The student entered a Lexile"),),
        )
    log.info("onboarding lexile saved")
    return app.ledger.finish(operation, learner_id, {"profile_ref": saved[0].ref.model_dump()})


def placement_demand(
    state_ref: RevisionRef, plan_ref: RevisionRef, profile_ref: RevisionRef
) -> DemandBody:
    """Rule-made: no planning call before anything was observed."""
    return DemandBody(
        status="checked",
        state_ref=state_ref,
        plan_ref=plan_ref,
        profile_ref=profile_ref,
        primary_goal=Goal(
            dimension_id="english.reading.facts",
            description="Read a short story at your Lexile; find facts and evidence",
        ),
        auxiliary_goal=Goal(
            dimension_id="english.speaking.pronunciation",
            description="Read a part of the story aloud for a first pronunciation sample",
        ),
        budget_minutes=30,
        prerequisites=(
            "The starting point is unknown; first answers without hints are the sample.",
        ),
        sampling_requirements=("Keep first answers and new tries after help apart.",),
        help_requirements=(
            "Two requests for help get different guidance; with outside "
            "help unknown, nothing is called done alone.",
        ),
        offscreen_requirements="The placement lesson has no task away from the screen.",
        fallback="Answers are typed or clicked; reading aloud is scored on this computer.",
        gaps=("Placement lesson: sampled at your Lexile; the result is only a start.",),
        purpose="placement",
    )


def save_chat(app, request_id: str, learner_id: str, answers: list[str]) -> dict:
    signature = {"answers": list(answers)}
    completed = app.ledger.completed_request(request_id, learner_id, "chat", signature)
    if completed:
        return completed
    prior = profile(app, learner_id)
    if prior is None or not prior.body.get("lexile"):
        raise LookupError("Enter your Lexile first, then chat with Jane.")
    if len(answers) != len(QUESTIONS) or any(not item.strip() for item in answers):
        raise ValueError("Please answer every question.")
    if prior.body.get("chat"):
        raise Conflict("The chat is already saved.", prior.ref)
    state, plan = app.current(learner_id, "state"), app.current(learner_id, "plan:week")
    operation = app.request(
        request_id, learner_id, "chat", signature, baselines=(prior.ref, state.ref, plan.ref)
    )
    saved = app.saved_step(operation, learner_id, "placement")
    if saved is None:
        chat = tuple(
            ChatTurn(question=question, answer=reply.strip())
            for question, reply in zip(QUESTIONS, answers, strict=True)
        )
        body = ProfileBody.model_validate(prior.body).model_copy(update={"chat": chat})
        obj = revised(operation, learner_id, prior, body)
        demand = document(
            "demand",
            identity(learner_id, "demand"),
            operation,
            learner_id,
            placement_demand(state.ref, plan.ref, obj.ref),
            refs=(state.ref, plan.ref, obj.ref),
        )
        saved = app.save_step(
            operation,
            learner_id,
            "placement",
            [obj, demand],
            publications=(
                publication(obj, "The student answered the chat questions"),
                publication(demand, "The placement lesson plan from the rules"),
            ),
            baselines=(prior.ref, state.ref, plan.ref),
        )
    log.info("onboarding chat saved answers=%d", len(answers))
    return app.ledger.finish(
        operation,
        learner_id,
        {"profile_ref": saved[0].ref.model_dump(), "demand_ref": saved[1].ref.model_dump()},
    )


def placement_activity(app, learner_id: str) -> tuple[str, Document | None] | None:
    """The sealed placement lesson and its current evaluation, if it was taught."""
    for fact in app.store.list_current(learner_id, "fact"):
        course = app.store.read(RevisionRef.model_validate(fact.body["course_ref"]), learner_id)
        demand = app.store.read(RevisionRef.model_validate(course.body["demand_ref"]), learner_id)
        if demand.body.get("purpose") == "placement":
            activity = fact.body["activity_id"]
            return activity, app.store.current(identity(activity, "evaluation"), learner_id)
    return None


def profile_request(body: ProfileBody, evaluation: dict) -> ModelRequest:
    return ModelRequest(
        "planning",
        PROFILE_INSTRUCTIONS,
        {
            "lexile": body.lexile,
            "chat": [turn.model_dump() for turn in body.chat],
            "evaluation": evaluation_context(evaluation),
        },
        ProfileDraft,
        180,
    )


async def summarize_profile(app, request_id: str, learner_id: str, activity_id: str) -> dict:
    signature = {"activity_id": activity_id}
    completed = app.ledger.completed_request(request_id, learner_id, "profile_summary", signature)
    if completed:
        return completed
    prior = profile(app, learner_id)
    evaluation = app.store.current(identity(activity_id, "evaluation"), learner_id)
    if prior is None or evaluation is None:
        raise LookupError("The placement lesson has no evaluation yet.")
    operation = app.request(
        request_id,
        learner_id,
        "profile_summary",
        signature,
        sources=(evaluation.ref,),
        baselines=(prior.ref,),
    )
    saved = app.saved_step(operation, learner_id, "profile")
    if saved is None:
        app.models.require()
        started = time.perf_counter()
        body = ProfileBody.model_validate(prior.body)
        reply = await app.models.run(profile_request(body, evaluation.body))
        if not reply.ok:
            log.warning("profile summary failure=%s", reply.failure)
            raise PlanningUnavailable(reply.failure, "profile")
        draft = reply.output
        if chinese(draft.model_dump()):
            log.warning("profile summary failure=language")
            raise PlanningUnavailable("language", "profile")
        update = draft.model_dump(exclude={"note"}) | {"summarized": True}
        obj = revised(operation, learner_id, prior, body.model_copy(update=update))
        saved = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "The profile Jane wrote, waiting for the student"),),
        )
        log.info("profile summarized elapsed_ms=%.0f", (time.perf_counter() - started) * 1000)
    return app.ledger.finish(operation, learner_id, {"profile_ref": saved[0].ref.model_dump()})


def confirm_profile(app, request_id: str, learner_id: str, edit: ProfileEdit) -> dict:
    """The student confirms or changes the profile; during onboarding this also accepts the
    placement evaluation, so the first plan can follow."""
    signature = edit.model_dump(mode="json")
    completed = app.ledger.completed_request(request_id, learner_id, "profile", signature)
    if completed:
        return completed
    prior = profile(app, learner_id)
    if prior is None:
        raise LookupError("There is no profile yet.")
    lexile = normalize_lexile(edit.lexile) if edit.lexile else None
    operation = app.request(request_id, learner_id, "profile", signature, baselines=(prior.ref,))
    saved = app.saved_step(operation, learner_id, "profile")
    if saved is None:
        base = ProfileBody.model_validate(prior.body)
        update = edit.model_dump(exclude={"lexile", "challenge"}) | {"confirmed": True}
        update |= {"lexile": lexile} if lexile else {}
        update |= {"challenge": edit.challenge} if edit.challenge else {}
        body = base.model_copy(update=update)
        obj = revised(operation, learner_id, prior, body)
        saved = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "The student confirmed the profile"),),
        )
    placed = placement_activity(app, learner_id)
    if placed and placed[1] is not None and app.acknowledged(learner_id, placed[0]) is None:
        app.acknowledge(f"{request_id}:placement", learner_id, placed[0], placed[1].ref)
    edited = (
        any(
            prior.body.get(key) != value
            for key, value in edit.model_dump(exclude={"lexile", "challenge"}).items()
        )
        or (lexile is not None and lexile != prior.body.get("lexile"))
        or (edit.challenge is not None and edit.challenge != prior.body.get("challenge"))
    )
    log.info("profile confirmed edited=%s", "true" if edited else "false")
    return app.ledger.finish(operation, learner_id, {"profile_ref": saved[0].ref.model_dump()})
