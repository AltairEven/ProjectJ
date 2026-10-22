"""P1a body mapping of the existing shared contract; no learning inference."""

import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from project_j.contracts import Contract, RevisionRef

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
    "verbs": ("Word types", "Verbs", "What action words mean and go with"),
    "adjectives": ("Word types", "Adjectives", "Words that describe, and where they go"),
    "adverbs": ("Word types", "Adverbs", "How, how often and how much"),
    "nouns": ("Word types", "One and many", "Plural nouns and nouns you cannot count"),
    "prepositions": ("Word types", "Prepositions", "Words for time, place and direction"),
    "articles": ("Word types", "A, an, the", "When to use a, an and the"),
    "present_simple": ("Tenses", "Present simple", "Habits, facts, and he/she/it with -s"),
    "past_simple": ("Tenses", "Past simple", "Things that happened; regular and irregular verbs"),
    "present_continuous": ("Tenses", "Present continuous", "Be + -ing for what is happening now"),
    "future": ("Tenses", "Future", "Will and be going to"),
    "present_perfect": ("Tenses", "Present perfect", "Have/has + past participle"),
    "comparatives": ("Sentences", "Comparing", "Bigger, the biggest"),
    "questions": ("Sentences", "Questions", "Word order in yes/no and wh- questions"),
}
MAX_FOCUS = 3


class FocusSuggestion(Contract):
    skill_id: Text
    reason: Text


Topic = Annotated[str, Field(min_length=1, max_length=100)]


class TopicSuggestion(Contract):
    """One story topic planning lists for the student to pick before preparation."""

    title: Topic
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
    # The story topic the student picked or wrote for this lesson; None not chosen yet.
    topic: Topic | None = None
    topic_suggestions: tuple[TopicSuggestion, ...] = Field(default=(), max_length=4)

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
        raise ValueError(
            "Write a Lexile as a number and L (like 650L) or BR, a number and L (like BR100L)"
        )
    return value


# How far above the student's Lexile stories are written (ADR-0023).
Challenge = Literal["same", "little", "challenge", "harder"]
CHALLENGE_STEPS: dict[str, int] = {"same": 0, "little": 50, "challenge": 100, "harder": 150}
DEFAULT_CHALLENGE: Challenge = "challenge"
Rating = Literal["too_hard", "just_right", "too_easy"]


def rated(challenge: Challenge, rating: Rating) -> Challenge:
    """One step down, none or one step up; the ends stay where they are."""
    names = list(CHALLENGE_STEPS)
    move = {"too_hard": -1, "just_right": 0, "too_easy": 1}[rating]
    return names[min(max(names.index(challenge) + move, 0), len(names) - 1)]


def challenge_of(profile: dict) -> Challenge:
    return profile.get("challenge") or DEFAULT_CHALLENGE


class ChatTurn(Contract):
    question: Text
    answer: Text


class ProfileBody(Contract):
    """The learner's own account, read by planning and preparation (ADR-0018)."""

    status: Literal["checked"] = "checked"
    confirmed: bool = False  # The student confirmed it (onboarding ends there).
    lexile: Text | None = None
    challenge: Challenge | None = None  # None: the default step.
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
