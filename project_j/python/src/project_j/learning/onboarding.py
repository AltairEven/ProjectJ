"""Onboarding (ADR-0018): Lexile, a short chat with Jane, a placement lesson, the profile.

The chat is the student's own account, kept word for word and never scored. The placement
lesson is an ordinary prepared lesson; its evaluation is accepted with the profile.
"""

import logging
import time

from project_j.application.documents import document, identity, publication
from project_j.contracts import Contract, Document, RevisionRef
from project_j.learning.contracts import (
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
    "What do you want to use English for? / 你学英语想用来做什么？",
    "What do you like to do in your free time? / 平时喜欢做什么，对什么感兴趣？",
    "How much time can you study each week? / 每周大概能用多少时间学习？",
    "What do you most want to get better at: reading, speaking, listening or pronunciation? "
    "/ 最想加强哪方面：阅读、口语、听力还是发音？",
)

PROFILE_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的AI教师，不是学生；学习者不具名，一律称“学生”。"
    "根据chat里学生对Jane固定问题的原话、lexile与evaluation（建档小测的评价），整理学习资料："
    "background写学习目的与当前英语使用情况，interests写兴趣，weekly_time写每周可用时间，"
    "focus写最想加强的方面。只写原话与评价支持的内容，不推测年龄、身份或能力等级，"
    "原话没说的写“未提到”。note用一句话告诉学生这份资料怎样用于选课。"
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
            publications=(publication(obj, "学生填写蓝思值"),),
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
            dimension_id="english.reading.facts", description="按蓝思值读一篇短文，找事实与依据"
        ),
        auxiliary_goal=Goal(
            dimension_id="english.speaking.pronunciation", description="朗读短文段落，取发音起点"
        ),
        budget_minutes=30,
        prerequisites=("起点未知，以未提示的初答取样。",),
        sampling_requirements=("保留未提示初答与帮助后新尝试。",),
        help_requirements=("两次求助用不同指导；外部帮助未知时不填独立。",),
        offscreen_requirements="建档小测不安排离屏任务。",
        fallback="作答以文字提交；朗读录音交本机发音评分。",
        gaps=("建档小测：按蓝思值取样，结果只作起点。",),
        purpose="placement",
    )


def save_chat(app, request_id: str, learner_id: str, answers: list[str]) -> dict:
    signature = {"answers": list(answers)}
    completed = app.ledger.completed_request(request_id, learner_id, "chat", signature)
    if completed:
        return completed
    prior = profile(app, learner_id)
    if prior is None or not prior.body.get("lexile"):
        raise LookupError("先填蓝思值，再和Jane聊天。")
    if len(answers) != len(QUESTIONS) or any(not item.strip() for item in answers):
        raise ValueError("每个问题都要回答。")
    if prior.body.get("chat"):
        raise Conflict("建档聊天已经保存。", prior.ref)
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
                publication(obj, "学生回答建档问题"),
                publication(demand, "规则生成的建档小测需求"),
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
        raise LookupError("建档小测还没有评价。")
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
            raise PlanningUnavailable(reply.failure, "学习资料")
        draft = reply.output
        update = draft.model_dump(exclude={"note"}) | {"summarized": True}
        obj = revised(operation, learner_id, prior, body.model_copy(update=update))
        saved = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "AI整理的学习资料，待学生确认"),),
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
        raise LookupError("还没有学习资料。")
    lexile = normalize_lexile(edit.lexile) if edit.lexile else None
    operation = app.request(request_id, learner_id, "profile", signature, baselines=(prior.ref,))
    saved = app.saved_step(operation, learner_id, "profile")
    if saved is None:
        base = ProfileBody.model_validate(prior.body)
        update = edit.model_dump(exclude={"lexile"}) | {"confirmed": True}
        body = base.model_copy(update=update | ({"lexile": lexile} if lexile else {}))
        obj = revised(operation, learner_id, prior, body)
        saved = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "学生确认学习资料"),),
        )
    placed = placement_activity(app, learner_id)
    if placed and placed[1] is not None and app.acknowledged(learner_id, placed[0]) is None:
        app.acknowledge(f"{request_id}:placement", learner_id, placed[0], placed[1].ref)
    edited = any(
        prior.body.get(key) != value for key, value in edit.model_dump(exclude={"lexile"}).items()
    ) or (lexile is not None and lexile != prior.body.get("lexile"))
    log.info("profile confirmed edited=%s", "true" if edited else "false")
    return app.ledger.finish(operation, learner_id, {"profile_ref": saved[0].ref.model_dump()})
