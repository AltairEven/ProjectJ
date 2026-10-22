"""The planning model proposes the next demand and plan reviews; rules decide what applies."""

from typing import Literal

from pydantic import Field

from project_j.contracts import Contract, RevisionRef
from project_j.evaluation.assessment import FAILURE_TEXT
from project_j.learning.contracts import (
    SKILLS,
    DemandBody,
    FocusSuggestion,
    Goal,
    PlanBody,
    PlanNote,
    ProposedCapability,
    SuggestionChoice,
    Text,
    TopicSuggestion,
)
from project_j.platform.models.contracts import ModelRequest

PLANNING_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的AI教师，不是学生；学习者在上下文中不具名，一律称“学生”。"
    "为下一课提出学习需求。只依据上下文里的本课评价、学习状态、各层计划、"
    "学习资料（profile）、能力目录、候选建议与当前需求；不补造未观察的表现，不重新评分，"
    "不宣布掌握、保持或迁移，未知保持未知。不预设学生的年龄，难度与内容按学习资料和状态决定。"
    "primary_goal与auxiliary_goal的dimension_id取catalog里已有的dimension_id，两者不同；"
    "需要目录外的英语能力时，把它写进new_capabilities（dimension_id以english.开头、不与目录重复，"
    "name为名称，definition写可观察的行为），目标才可以用这个编号；周计划补丁仍只用目录里已有的编号。"
    "goal_reason写为什么选这两个目标；与当前需求不同时说明为什么换，换目标要等学生确认。"
    "上下文有keep_goals时，学生不同意换目标：主要与辅助目标必须与keep_goals相同。"
    "direction：consolidate巩固，review复查，extend拓展，advance小步进阶，observe短观察；"
    "一次通常只增加一种主要难度；需要帮助的先改变支持并留独立尝试。"
    "预算固定30分钟，不改预算，不把时间排满。"
    "scores是最近几节课按题型与能力的平均分（10分制，未知不计）与题数：参考它决定下节课的目标与加强方向；"
    "分数低不等于没学会，结合评价的依据与限制判断，题数少于3只作参考。"
    "suggestion_action对候选建议给adopt、modify、defer或reject，suggestion_reason写理由。"
    "周计划可小调整：plan_effect为adjusted时plan_patch只改main_goal、verification或exit_conditions，"
    "plan_reason写如何服务当前重点；不需要调整时为reused。阶段、年度、长期的变化不在这里改，写进gaps。"
    "note给学生：before原安排，after新安排，why为什么，uncertain还不确定的事，expectation下节预期。"
    "focus_suggestions从skills里选0–2项建议下节加强的语言项目（学生会在备课前自己勾选），"
    "reason写评价里实际观察到的依据；没有依据就不建议。"
    "topic_suggestions给3–4个下一课短文的题材供学生挑选（学生也可以自己写）：title一句短标题，"
    "reason写为什么（如出自学生的哪项兴趣）；从profile.interests出发，可以延伸到相邻的方面，"
    "彼此要有区别；避开recent_titles里最近几课的题材；难度适合profile.lexile与本课目标。"
    "所有写给学生的文字（goal_reason、suggestion_reason、plan_reason、note、各reason、title、"
    "new_capabilities的name与definition、gaps）一律用简单英文，不夹中文字或中文标点，"
    "用词和句长不超过profile.lexile（为null时按初学者写短句、常用词）。"
)

REVIEW_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的AI教师，不是学生；学习者在上下文中不具名，一律称“学生”。"
    "为指定层级的计划做复盘。只依据上下文里的计划、学习状态、"
    "已有评价与学习资料（profile）；复盘不算新的学习活动，不重复计样本，日期和课时不产生完成或掌握。"
    "conclusion写继续、缩小、观察、调整或结束阶段的理由；unknown列仍未知的事；"
    "next_review_condition写下次复查需要什么新依据。缺少依据时可以暂不调整，写明下次观察什么。"
    "plan_effect为adjusted时patch只改main_goal、verification或exit_conditions；"
    "main_goal的dimension_id只能取catalog里已有的；预算固定30分钟不改。"
    "周计划的小调整会直接生效；阶段、年度、长期的变化交学生决定，"
    "reason写依据，impact写影响，alternatives列其他选择。"
    "所有写给学生的文字一律用简单英文，不夹中文字或中文标点，"
    "用词和句长不超过profile.lexile（为null时按初学者写短句、常用词）。"
)

Direction = Literal["consolidate", "review", "extend", "advance", "observe"]


class PlanPatch(Contract):
    main_goal: Goal | None = None
    verification: Text | None = None
    exit_conditions: Text | None = None
    budget_minutes: int | None = None  # Visible only so the rules can refuse it.


class TopicDraft(Contract):
    """No length limit in the output schema; the rules drop a title that does not fit."""

    title: Text
    reason: Text


class DemandDraft(Contract):
    suggestion_action: Literal["adopt", "modify", "defer", "reject"]
    suggestion_reason: Text
    direction: Direction
    primary_goal: Goal
    auxiliary_goal: Goal
    prerequisites: tuple[Text, ...]
    sampling_requirements: tuple[Text, ...] = Field(min_length=1)
    help_requirements: tuple[Text, ...] = Field(min_length=1)
    offscreen_requirements: Text
    fallback: Text
    gaps: tuple[Text, ...]
    plan_effect: Literal["reused", "adjusted"]
    plan_reason: Text
    plan_patch: PlanPatch | None
    note: PlanNote
    goal_reason: Text
    new_capabilities: tuple[ProposedCapability, ...] = ()
    focus_suggestions: tuple[FocusSuggestion, ...] = ()
    topic_suggestions: tuple[TopicDraft, ...] = ()


class ReviewDraft(Contract):
    conclusion: Text
    unknown: tuple[Text, ...]
    next_review_condition: Text
    plan_effect: Literal["reused", "adjusted"]
    patch: PlanPatch | None
    reason: Text
    impact: Text
    alternatives: tuple[Text, ...]


def pick(body: dict, *keys: str) -> dict:
    return {key: body.get(key) for key in keys}


def learning_context(state: dict, plans: dict[str, dict], profile: dict, catalog: list[dict]):
    """Learning data only: no learner, provider or object identity reaches the model."""
    return {
        "state": [
            pick(item, "dimension_id", "judgment", "help", "task_scope", "limitations")
            for item in state["dimensions"]
        ],
        "plans": {
            level: pick(
                body,
                "main_goal",
                "verification",
                "exit_conditions",
                "budget_minutes",
                "limitations",
            )
            for level, body in plans.items()
        },
        "profile": pick(
            profile,
            "lexile",
            "background",
            "interests",
            "weekly_time",
            "focus",
            "materials",
            "limitations",
        ),
        # "name" is a guarded identity key in model requests; a capability's name is its label.
        "catalog": [
            {"dimension_id": item["dimension_id"], "label": item["name"]} | pick(item, "definition")
            for item in catalog
        ],
    }


def evaluation_context(body: dict) -> dict:
    return {
        "report": body.get("report"),
        "tasks": [
            pick(task, "task_id", "dimension_id", "working_judgment", "explanation")
            for task in body["tasks"]
        ],
        "limitations": body.get("limitations"),
    }


def demand_request(
    evaluation: dict,
    state: dict,
    plans: dict[str, dict],
    profile: dict,
    catalog: list[dict],
    suggestion: dict,
    demand: dict,
    recent_titles: list[str],
    scores: dict | None = None,
) -> ModelRequest:
    material = suggestion["material"]
    return ModelRequest(
        "planning",
        PLANNING_INSTRUCTIONS,
        learning_context(state, plans, profile, catalog)
        | {
            "evaluation": evaluation_context(evaluation),
            "suggestion": pick(suggestion, "direction", "primary_change", "sampling", "help")
            | pick(suggestion, "exit", "review")
            | {"material": pick(material, "title", "focus", "familiar")},
            "demand": pick(
                demand,
                "primary_goal",
                "auxiliary_goal",
                "budget_minutes",
                "prerequisites",
                "sampling_requirements",
                "help_requirements",
                "offscreen_requirements",
                "fallback",
                "gaps",
            ),
            "recent_titles": recent_titles,
            "scores": scores or {},
            "skills": [
                {"skill_id": skill, "group": group, "label": label, "hint": hint}
                for skill, (group, label, hint) in SKILLS.items()
            ],
        },
        DemandDraft,
        180,
    )


def review_request(
    level: str,
    plans: dict[str, dict],
    state: dict,
    evaluations: list[dict],
    profile: dict,
    catalog: list[dict],
) -> ModelRequest:
    return ModelRequest(
        "planning",
        REVIEW_INSTRUCTIONS,
        learning_context(state, plans, profile, catalog)
        | {"level": level, "evaluations": [evaluation_context(body) for body in evaluations]},
        ReviewDraft,
        180,
    )


class PlanningUnavailable(Exception):
    """Nothing of this plan or demand was saved; earlier saved steps stay and it can retry."""

    def __init__(self, reason: str, subject: str = "next lesson's plan"):
        text = FAILURE_TEXT.get(reason, reason)
        kept = {
            "next lesson's plan": "The evaluation and learning state are saved",
            "profile": "The chat and the placement lesson are saved",
        }.get(subject, "The earlier plan stays")
        super().__init__(f"The {subject} could not be made ({text}). {kept}; you can try again.")


def checked_plan(body: dict, catalog_ids: set[str]) -> PlanBody:
    data = PlanBody.model_validate(body)
    if data.main_goal.dimension_id not in catalog_ids:
        # Rewording a goal never creates a capability; new identities go through the catalog.
        raise ValueError("plan goal must use an established capability identity")
    if data.budget_minutes != 30:
        raise ValueError(
            "the adopted MVP remains 30 minutes; "
            "a different baseline needs a separate design decision"
        )
    return data


def needs_goal_confirmation(previous: DemandBody, new: DemandBody) -> bool:
    """The student confirms the first goal after onboarding, a changed goal, new capabilities."""
    return (
        previous.purpose == "placement"
        or (new.primary_goal.dimension_id, new.auxiliary_goal.dimension_id)
        != (previous.primary_goal.dimension_id, previous.auxiliary_goal.dimension_id)
        or bool(new.proposed_capabilities)
    )


def apply_demand_draft(
    draft: DemandDraft,
    current: DemandBody,
    catalog_ids: set[str],
    state_ref: RevisionRef,
    plan_ref: RevisionRef,
    suggestion_ref: RevisionRef,
    plan_adjusted: bool,
) -> DemandBody:
    """The model's demand as a checked revision; an unknown or repeated goal voids the draft."""
    proposed = {item.dimension_id for item in draft.new_capabilities}
    if any(item in catalog_ids or not item.startswith("english.") for item in proposed) or len(
        proposed
    ) != len(draft.new_capabilities):
        raise PlanningUnavailable("a new skill entry is not valid")
    goals = (draft.primary_goal.dimension_id, draft.auxiliary_goal.dimension_id)
    if any(goal not in catalog_ids | proposed for goal in goals):
        raise PlanningUnavailable("a goal is not in the skill list")
    if goals[0] == goals[1]:
        raise PlanningUnavailable("the two goals are the same")
    # A proposal no goal uses is dropped; the catalog only grows by what a lesson needs.
    used = tuple(item for item in draft.new_capabilities if item.dimension_id in goals)
    body = DemandBody(
        status="checked",
        state_ref=state_ref,
        plan_ref=plan_ref,
        profile_ref=current.profile_ref,
        primary_goal=draft.primary_goal,
        auxiliary_goal=draft.auxiliary_goal,
        budget_minutes=30,
        prerequisites=draft.prerequisites,
        sampling_requirements=draft.sampling_requirements,
        help_requirements=draft.help_requirements,
        offscreen_requirements=draft.offscreen_requirements,
        fallback=draft.fallback,
        gaps=draft.gaps,
        suggestion_choices=(
            SuggestionChoice(
                suggestion_ref=suggestion_ref,
                action=draft.suggestion_action,
                reason=draft.suggestion_reason,
            ),
        ),
        direction=draft.direction,
        # What happened to the plan, not what the draft claimed.
        plan_effect="adjusted" if plan_adjusted else "reused",
        plan_reason=draft.plan_reason,
        note=draft.note,
        proposed_capabilities=used,
        goal_reason=draft.goal_reason,
        # Only known skills, at most two; a stray one never voids the plan.
        focus_suggestions=tuple(
            item for item in draft.focus_suggestions if item.skill_id in SKILLS
        )[:2],
        # A title that does not fit is dropped, at most four; neither voids the plan.
        topic_suggestions=tuple(
            TopicSuggestion(title=item.title.strip(), reason=item.reason)
            for item in draft.topic_suggestions
            if 0 < len(item.title.strip()) <= 100
        )[:4],
    )
    needed = needs_goal_confirmation(current, body)
    return body.model_copy(update={"goal_confirmation": "needed" if needed else None})


def week_plan_after(draft: DemandDraft, plan: dict, catalog_ids: set[str]) -> PlanBody | None:
    """An authorized small week adjustment, or None to keep the plan; budget never changes."""
    if draft.plan_effect != "adjusted" or draft.plan_patch is None:
        return None
    patch = draft.plan_patch.model_dump(mode="json", exclude_none=True, exclude={"budget_minutes"})
    if not patch:
        return None
    try:
        return checked_plan(plan | patch, catalog_ids)
    except ValueError as error:
        raise PlanningUnavailable("a week plan goal is not in the skill list") from error


def apply_review_draft(
    level: str, draft: ReviewDraft, plan: dict, catalog_ids: set[str]
) -> tuple[PlanBody | None, dict | None, list[str]]:
    """(week plan that applies now, patch waiting for the student, rule notes)."""
    checks: list[str] = []
    if draft.plan_effect != "adjusted" or draft.patch is None:
        return None, None, checks
    patch = draft.patch.model_dump(mode="json", exclude_none=True)
    if patch.pop("budget_minutes", None) is not None:
        checks.append("The time is fixed at 30 minutes; the change does not apply.")
    if not patch:
        return None, None, checks
    try:
        body = checked_plan(plan | patch, catalog_ids)
    except ValueError:
        checks.append(
            "The goal is not in the skill list; the change is dropped and the plan stays."
        )
        return None, None, checks
    # Week adjustments are within the standing authorization; higher levels are the student's.
    return (body, None, checks) if level == "week" else (None, patch, checks)
