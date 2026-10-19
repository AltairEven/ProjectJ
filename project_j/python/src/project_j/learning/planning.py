"""The planning model proposes the next demand and plan reviews; rules decide what applies."""

from typing import Literal

from pydantic import Field

from project_j.contracts import Contract, RevisionRef
from project_j.evaluation.assessment import FAILURE_TEXT
from project_j.learning.contracts import (
    DemandBody,
    Goal,
    ParentNote,
    PlanBody,
    SuggestionChoice,
    Text,
)
from project_j.platform.models.contracts import ModelRequest

PLANNING_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的教师，不是学生；学习者在上下文中不具名，一律称“孩子”。"
    "为下一课提出学习需求。只依据上下文里的本课评价、学习状态、各层计划、"
    "家庭条件、能力目录、候选建议与当前需求；不补造未观察的表现，不重新评分，"
    "不宣布掌握、保持或迁移，未知保持未知。"
    "primary_goal与auxiliary_goal的dimension_id只能取catalog里已有的dimension_id，两者不同；"
    "需要目录外的能力时不要编造编号，写进gaps。"
    "direction：consolidate巩固，review复查，extend拓展，advance小步进阶，observe短观察；"
    "一次通常只增加一种主要难度；需要帮助的先改变支持并留独立尝试。"
    "预算固定30分钟，不改预算，不把时间排满。"
    "suggestion_action对候选建议给adopt、modify、defer或reject，suggestion_reason写理由。"
    "周计划可小调整：plan_effect为adjusted时plan_patch只改main_goal、verification或exit_conditions，"
    "plan_reason写如何服务当前重点；不需要调整时为reused。阶段、年度、长期的变化不在这里改，写进gaps。"
    "note给家长：before原安排，after新安排，why为什么，uncertain还不确定的事，expectation下节预期。"
    "评价或状态写明成人或模拟来源时，说明不能作为儿童学习证据。"
)

REVIEW_INSTRUCTIONS = (
    "你是学习规划助手。Jane是唯一的教师，不是学生；学习者在上下文中不具名，一律称“孩子”。"
    "为指定层级的计划做复盘。只依据上下文里的计划、学习状态、"
    "已有评价与家庭条件；复盘不算新的学习活动，不重复计样本，日期和课时不产生完成或掌握。"
    "conclusion写继续、缩小、观察、调整或结束阶段的理由；unknown列仍未知的事；"
    "next_review_condition写下次复查需要什么新依据。缺少依据时可以暂不调整，写明下次观察什么。"
    "plan_effect为adjusted时patch只改main_goal、verification或exit_conditions；"
    "main_goal的dimension_id只能取catalog里已有的；预算固定30分钟不改。"
    "周计划的小调整会直接生效；阶段、年度、长期的变化交家长决定，"
    "reason写依据，impact写影响，alternatives列其他选择。"
    "评价或状态写明成人或模拟来源时，说明不能作为儿童学习证据。"
)

Direction = Literal["consolidate", "review", "extend", "advance", "observe"]


class PlanPatch(Contract):
    main_goal: Goal | None = None
    verification: Text | None = None
    exit_conditions: Text | None = None
    budget_minutes: int | None = None  # Visible only so the rules can refuse it.


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
    note: ParentNote


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


def learning_context(state: dict, plans: dict[str, dict], household: dict, catalog: list[dict]):
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
        "household": pick(household, "raw_report", "limitations"),
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
    household: dict,
    catalog: list[dict],
    suggestion: dict,
    demand: dict,
) -> ModelRequest:
    material = suggestion["material"]
    return ModelRequest(
        "planning",
        PLANNING_INSTRUCTIONS,
        learning_context(state, plans, household, catalog)
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
        },
        DemandDraft,
        180,
    )


def review_request(
    level: str,
    plans: dict[str, dict],
    state: dict,
    evaluations: list[dict],
    household: dict,
    catalog: list[dict],
) -> ModelRequest:
    return ModelRequest(
        "planning",
        REVIEW_INSTRUCTIONS,
        learning_context(state, plans, household, catalog)
        | {"level": level, "evaluations": [evaluation_context(body) for body in evaluations]},
        ReviewDraft,
        180,
    )


class PlanningUnavailable(Exception):
    """Nothing of this plan or demand was saved; earlier saved steps stay and it can retry."""

    def __init__(self, reason: str, subject: str = "下节需求"):
        text = FAILURE_TEXT.get(reason, reason)
        kept = "评价与学习状态已保存" if subject == "下节需求" else "原计划不变"
        super().__init__(f"{subject}未取得（{text}）。{kept}，可以重试。")


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
    goals = (draft.primary_goal.dimension_id, draft.auxiliary_goal.dimension_id)
    if any(goal not in catalog_ids for goal in goals):
        raise PlanningUnavailable("目标不在能力目录")
    if goals[0] == goals[1]:
        raise PlanningUnavailable("主要与辅助目标相同")
    return DemandBody(
        status="checked",
        state_ref=state_ref,
        plan_ref=plan_ref,
        household_ref=current.household_ref,
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
    )


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
        raise PlanningUnavailable("周计划目标不在能力目录") from error


def apply_review_draft(
    level: str, draft: ReviewDraft, plan: dict, catalog_ids: set[str]
) -> tuple[PlanBody | None, dict | None, list[str]]:
    """(week plan that applies now, patch waiting for the parent, rule notes)."""
    checks: list[str] = []
    if draft.plan_effect != "adjusted" or draft.patch is None:
        return None, None, checks
    patch = draft.patch.model_dump(mode="json", exclude_none=True)
    if patch.pop("budget_minutes", None) is not None:
        checks.append("预算固定30分钟，改动不生效。")
    if not patch:
        return None, None, checks
    try:
        body = checked_plan(plan | patch, catalog_ids)
    except ValueError:
        checks.append("计划目标不在能力目录，补丁作废，沿用原计划。")
        return None, None, checks
    # Week adjustments are within the standing authorization; higher levels are the parent's.
    return (body, None, checks) if level == "week" else (None, patch, checks)
