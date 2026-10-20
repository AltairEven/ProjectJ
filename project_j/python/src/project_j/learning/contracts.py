"""P1a body mapping of the existing shared contract; no learning inference."""

import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from project_j.contracts import Contract, ObjectEnvelope, RevisionRef

Text = Annotated[str, Field(min_length=1)]
Help = Literal["none", "light_prompt", "scaffold", "full_model", "unknown"]


class Goal(Contract):
    dimension_id: Text
    description: Text


class PlanNote(Contract):
    """What the student reads about one revision: before, after, why, unknowns, next lesson."""

    before: Text
    after: Text
    why: Text
    uncertain: tuple[Text, ...]
    expectation: Text


class DimensionState(Contract):
    dimension_id: Text
    task_scope: Text
    judgment: Literal[
        "unknown", "needs_help", "single_independent", "multiple_dates_independent", "inconsistent"
    ]
    evidence_refs: tuple[RevisionRef, ...] = ()
    help: Help
    material_condition: Text
    observed_at: AwareDatetime | None
    retention: Text | None = None
    transfer: Text | None = None
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def evidence_conditions(self) -> Self:
        if self.judgment != "unknown" and not self.evidence_refs:
            raise ValueError("a working judgment needs saved evidence references")
        if self.judgment in {"single_independent", "multiple_dates_independent"}:
            if self.help != "none":
                raise ValueError("unknown or content help cannot be called independent")
        if (self.judgment == "unknown" or self.observed_at is None) and not self.limitations:
            raise ValueError("unknown observation needs an explicit limitation")
        return self


class StateBody(Contract):
    status: Literal["candidate", "checked"]
    dimensions: tuple[DimensionState, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_dimensions(self) -> Self:
        if len({item.dimension_id for item in self.dimensions}) != len(self.dimensions):
            raise ValueError("duplicate state dimension")
        return self


class PlanBody(Contract):
    status: Literal["draft", "waiting_decision", "checked", "limited"]
    level: Literal["long_term", "annual", "stage", "week"]
    parent_ref: RevisionRef | None
    main_goal: Goal
    budget_minutes: int = Field(ge=1, strict=True)
    basis_refs: tuple[RevisionRef, ...]
    verification: Text
    exit_conditions: Text
    review_at: AwareDatetime | None
    decision_ref: RevisionRef | None = None
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def missing_parent(self) -> Self:
        if self.level != "long_term" and self.parent_ref is None and not self.limitations:
            raise ValueError("missing upper plan needs an explicit limitation")
        return self


class SuggestionChoice(Contract):
    suggestion_ref: RevisionRef
    action: Literal["adopt", "modify", "defer", "reject"]
    reason: Text


class ProposedCapability(Contract):
    """A capability outside the catalog that planning proposes with a goal (ADR-0018)."""

    dimension_id: Text
    name: Text
    definition: Text


# What the student may tick to train in the next lesson: id -> (group, name, hint for the model).
SKILLS: dict[str, tuple[str, str, str]] = {
    "verbs": ("词类", "动词", "实义动词的意思与搭配"),
    "adjectives": ("词类", "形容词", "形容词的意思、位置与描述"),
    "adverbs": ("词类", "副词", "副词修饰动作的方式、频率与程度"),
    "nouns": ("词类", "名词单复数", "可数名词单复数与不可数名词"),
    "prepositions": ("词类", "介词", "表示时间、地点与方向的介词"),
    "articles": ("词类", "冠词", "a、an与the的用法"),
    "present_simple": ("时态", "一般现在时", "习惯、事实与第三人称单数"),
    "past_simple": ("时态", "一般过去时", "过去发生的事，规则与不规则动词过去式"),
    "present_continuous": ("时态", "现在进行时", "be加动词ing表示正在进行"),
    "future": ("时态", "将来时", "will与be going to表示将来"),
    "present_perfect": ("时态", "现在完成时", "have/has加过去分词表示经历或结果"),
    "comparatives": ("句型", "比较级与最高级", "比较两个或多个事物"),
    "questions": ("句型", "疑问句", "一般疑问句与特殊疑问句的语序"),
}
MAX_FOCUS = 3


class FocusSuggestion(Contract):
    skill_id: Text
    reason: Text


class DemandBody(Contract):
    status: Literal["draft", "checked"]
    state_ref: RevisionRef
    plan_ref: RevisionRef
    profile_ref: RevisionRef
    primary_goal: Goal
    auxiliary_goal: Goal
    budget_minutes: Literal[30]
    prerequisites: tuple[Text, ...]
    sampling_requirements: tuple[Text, ...] = Field(min_length=1)
    help_requirements: tuple[Text, ...] = Field(min_length=1)
    offscreen_requirements: Text
    fallback: Text
    gaps: tuple[Text, ...]
    suggestion_choices: tuple[SuggestionChoice, ...] = ()
    # Set when the planning model proposed this revision and the rules accepted it.
    direction: Literal["consolidate", "review", "extend", "advance", "observe"] | None = None
    plan_effect: Literal["reused", "adjusted"] | None = None
    plan_reason: Text | None = None
    note: PlanNote | None = None
    # A placement lesson is the onboarding test; a goal change waits for the student.
    purpose: Literal["lesson", "placement"] = "lesson"
    goal_confirmation: Literal["needed", "confirmed"] | None = None
    proposed_capabilities: tuple[ProposedCapability, ...] = ()
    goal_reason: Text | None = None
    # The student's ticks for this lesson: None not chosen yet, () nothing in particular.
    skill_focus: tuple[str, ...] | None = None
    skill_note: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    focus_suggestions: tuple[FocusSuggestion, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def goals_and_choices(self) -> Self:
        if self.primary_goal == self.auxiliary_goal:
            raise ValueError("primary and auxiliary goals must be distinct")
        ticked = (*(self.skill_focus or ()), *(item.skill_id for item in self.focus_suggestions))
        if any(skill not in SKILLS for skill in ticked):
            raise ValueError("skills come from the fixed list")
        if self.skill_focus and len(set(self.skill_focus)) != len(self.skill_focus):
            raise ValueError("each skill is ticked once")
        if self.skill_focus and len(self.skill_focus) > MAX_FOCUS:
            raise ValueError(f"at most {MAX_FOCUS} skills a lesson")
        refs = [choice.suggestion_ref for choice in self.suggestion_choices]
        if len(set(refs)) != len(refs):
            raise ValueError("each suggestion has one adoption decision")
        return self


def normalize_lexile(raw: str) -> str:
    """A Lexile measure as "650L" or "BR100L"; anything else is refused, never guessed."""
    value = re.sub(r"\s+", "", raw).upper()
    if not re.fullmatch(r"(BR)?\d{1,4}L", value):
        raise ValueError("蓝思值写成“数字+L”（如650L）或“BR+数字+L”（如BR100L）")
    return value


class ChatTurn(Contract):
    question: Text
    answer: Text


class ProfileBody(Contract):
    """The learner's own account, read by planning and preparation (ADR-0018)."""

    status: Literal["checked"] = "checked"
    confirmed: bool = False  # The student confirmed it (onboarding ends there).
    lexile: Text | None = None
    chat: tuple[ChatTurn, ...] = ()
    background: Text | None = None
    interests: Text | None = None
    weekly_time: Text | None = None
    focus: Text | None = None
    materials: Text | None = None  # Usable things; none means no offscreen task.
    summarized: bool = False
    limitations: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def lexile_form(self) -> Self:
        if self.lexile is not None and normalize_lexile(self.lexile) != self.lexile:
            raise ValueError("lexile must be stored in its normal form")
        return self


class SimulationRecord(ObjectEnvelope):
    # ObjectEnvelope keeps realm and source paired.
    realm: Literal["simulation", "study"] = "simulation"
    source_type: Literal["simulated", "student"] = "simulated"
    owner: Literal["learning"] = "learning"
    received_at: AwareDatetime

    @model_validator(mode="after")
    def previous_revision(self) -> Self:
        expected = (
            RevisionRef(object_id=self.object_id, revision=self.revision - 1)
            if self.revision > 1
            else None
        )
        if self.supersedes != expected:
            raise ValueError("a revision must retain its exact predecessor")
        return self

    @property
    def references(self) -> tuple[RevisionRef, ...]:
        refs = self.dependency_refs + self.source_refs
        if self.supersedes:
            refs += (self.supersedes,)
        return refs


class LearningState(SimulationRecord):
    kind: Literal["state"] = "state"
    body: StateBody

    @property
    def references(self) -> tuple[RevisionRef, ...]:
        return super().references + tuple(
            ref for dimension in self.body.dimensions for ref in dimension.evidence_refs
        )


class LearningPlan(SimulationRecord):
    kind: Literal["plan"] = "plan"
    body: PlanBody

    @property
    def references(self) -> tuple[RevisionRef, ...]:
        return (
            super().references
            + self.body.basis_refs
            + tuple(ref for ref in (self.body.parent_ref, self.body.decision_ref) if ref)
        )


class LearningDemand(SimulationRecord):
    kind: Literal["demand"] = "demand"
    body: DemandBody

    @property
    def references(self) -> tuple[RevisionRef, ...]:
        return (
            super().references
            + (self.body.state_ref, self.body.plan_ref, self.body.profile_ref)
            + tuple(choice.suggestion_ref for choice in self.body.suggestion_choices)
        )


class LearnerProfile(SimulationRecord):
    kind: Literal["profile"] = "profile"
    body: ProfileBody


StoredRecord = Annotated[
    LearningState | LearningPlan | LearningDemand | LearnerProfile, Field(discriminator="kind")
]
record_adapter = TypeAdapter(StoredRecord)
