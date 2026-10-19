"""P1a body mapping of the existing shared contract; no learning inference."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from project_j.contracts import Contract, ObjectEnvelope, RevisionRef

Text = Annotated[str, Field(min_length=1)]
Help = Literal["none", "light_prompt", "scaffold", "full_model", "unknown"]


class Goal(Contract):
    dimension_id: Text
    description: Text


class ParentNote(Contract):
    """What the parent reads about one revision: before, after, why, unknowns, next lesson."""

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


class DemandBody(Contract):
    status: Literal["draft", "checked"]
    state_ref: RevisionRef
    plan_ref: RevisionRef
    household_ref: RevisionRef
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
    note: ParentNote | None = None

    @model_validator(mode="after")
    def goals_and_choices(self) -> Self:
        if self.primary_goal == self.auxiliary_goal:
            raise ValueError("primary and auxiliary goals must be distinct")
        refs = [choice.suggestion_ref for choice in self.suggestion_choices]
        if len(set(refs)) != len(refs):
            raise ValueError("each suggestion has one adoption decision")
        return self


class HouseholdBody(Contract):
    status: Literal["draft", "checked"]
    raw_report: Text
    provider: Text
    reported_at: AwareDatetime
    applies_to: tuple[Text, ...] = Field(min_length=1)
    limitations: tuple[Text, ...]


class SimulationRecord(ObjectEnvelope):
    # Simulation or adult check; ObjectEnvelope keeps realm and source paired, family is excluded.
    realm: Literal["simulation", "adult_check"] = "simulation"
    source_type: Literal["simulated", "adult"] = "simulated"
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
            + (self.body.state_ref, self.body.plan_ref, self.body.household_ref)
            + tuple(choice.suggestion_ref for choice in self.body.suggestion_choices)
        )


class HouseholdConditions(SimulationRecord):
    kind: Literal["household"] = "household"
    learner_id: None = None
    body: HouseholdBody


StoredRecord = Annotated[
    LearningState | LearningPlan | LearningDemand | HouseholdConditions, Field(discriminator="kind")
]
record_adapter = TypeAdapter(StoredRecord)
