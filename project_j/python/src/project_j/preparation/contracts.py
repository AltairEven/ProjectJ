from typing import Literal

from pydantic import Field, model_validator

from project_j.contracts import Contract, RevisionRef
from project_j.learning.contracts import SKILLS, Challenge, DemandBody, Goal, Help
from project_j.preparation.reading import MAX_SEGMENT_WORDS, sentences, words
from project_j.preparation.task_types import (
    CONTENT_TYPES,
    MAX_MINUTES,
    PROGRAM_JUDGED,
    ChoiceContent,
    FillContent,
    MatchContent,
    OpenContent,
    OrderContent,
)


def demand_requirements(demand: DemandBody) -> list[tuple[str, str]]:
    """Every demand item a course must implement or explicitly reuse with a reason."""
    items = [
        ("primary_goal", demand.primary_goal.description),
        ("auxiliary_goal", demand.auxiliary_goal.description),
        ("budget_minutes", str(demand.budget_minutes)),
        ("offscreen_requirements", demand.offscreen_requirements),
        ("fallback", demand.fallback),
    ]
    for field in ("prerequisites", "sampling_requirements", "help_requirements", "gaps"):
        items += [(field, text) for text in getattr(demand, field)]
    items += [
        (
            "suggestion_choices",
            f"{choice.action}:{choice.suggestion_ref.object_id}/{choice.suggestion_ref.revision}",
        )
        for choice in demand.suggestion_choices
    ]
    if demand.topic:  # The story is about what the student chose.
        items.append(("topic", demand.topic))
    # What the student ticked must show in the story, the questions and the help.
    items += [
        ("skill_focus", f"{SKILLS[skill][1]}: {SKILLS[skill][2]}")
        for skill in demand.skill_focus or ()
    ]
    if demand.skill_note:
        items.append(("skill_note", demand.skill_note))
    return items


class RequirementMapping(Contract):
    field: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    task_ids: tuple[str, ...] = ()


class Support(Contract):
    text: str = Field(min_length=1)
    help: dict[str, Help]


class TaskDefinition(Contract):
    task_id: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    purpose: Literal[
        "answer", "point", "offscreen", "read_aloud", "choice", "order", "match", "fill", "open"
    ]
    prompt: str = Field(min_length=1)
    material_ref: RevisionRef
    evidence: str = Field(min_length=1)
    acceptable_simulated: tuple[str, ...]
    incorrect_simulated: tuple[str, ...] = ()
    supports: tuple[Support, ...] = Field(min_length=2)
    exit_text: str = Field(min_length=1)
    optional: bool = False
    # Read-aloud only: the scoring segment's reference text and the reading it belongs to.
    reference_text: str | None = None
    reading_id: str | None = None
    # A task type's content sits in the field named after it (preparation/task_types.py).
    choice: ChoiceContent | None = None
    order: OrderContent | None = None
    match: MatchContent | None = None
    fill: FillContent | None = None
    open: OpenContent | None = None
    # What Jane says when a clicked task is answered right.
    correct_text: str | None = Field(default=None, min_length=1)
    minutes: int | None = None  # Offscreen only: its estimated minutes.
    type_version: int = Field(default=1, ge=1, strict=True)

    @model_validator(mode="after")
    def own_content(self):
        for code in CONTENT_TYPES:
            if (getattr(self, code) is not None) != (self.purpose == code):
                raise ValueError("a task carries exactly the content of its own type")
        if (self.correct_text is not None) != (self.purpose in PROGRAM_JUDGED):
            raise ValueError("only a clicked task says a prepared line when answered right")
        if self.minutes is not None and self.purpose != "offscreen":
            raise ValueError("only an offscreen task has its own minutes")
        return self

    @model_validator(mode="after")
    def different_supports(self):
        if len({support.text for support in self.supports}) != len(self.supports):
            raise ValueError("help branches must provide different actual guidance")
        return self

    @model_validator(mode="after")
    def reading_segment(self):
        if self.purpose != "read_aloud":
            if self.reference_text is not None or self.reading_id is not None:
                raise ValueError("only a read-aloud task carries a reference text")
            return self
        if not self.reference_text or not self.reading_id:
            raise ValueError("a read-aloud task needs its reference text and reading")
        sentences(self.reference_text)  # Raises unless it ends at a sentence end.
        if len(words(self.reference_text)) > MAX_SEGMENT_WORDS:
            raise ValueError("a scoring segment must stay within the segment word limit")
        return self


# The local synthesizer's single-request text limit (ADR-0016).
MAX_AUDIO_CHARS = 1000


class ResourceSpec(Contract):
    """A teaching resource the course asks for; generated only after the parent confirms."""

    resource_id: str = Field(min_length=1)
    kind: Literal["image", "audio", "animation", "video"]
    purpose: str = Field(min_length=1)
    content: str = Field(min_length=1)
    task_ids: tuple[str, ...]
    audience: Literal["student", "parent", "teacher"]
    required: bool
    fallback: str = Field(min_length=1)
    # When the student sees or hears it: with the question, as help after asking, beside a
    # story paragraph, or as one option of a picture-option question (ADR-0026).
    stage: Literal["question", "help", "story", "option"] = "question"
    # Audio only: the exact words Jane says, word for word the course text.
    text: str | None = Field(default=None, min_length=1, max_length=MAX_AUDIO_CHARS)
    paragraph: int | None = Field(default=None, ge=1)  # story: which paragraph, title not counted
    option: int | None = Field(default=None, ge=0)  # option: the index in choice.options
    # A still question picture that is part of the question itself, and so is checked.
    picture_question: bool = False

    @model_validator(mode="after")
    def spoken_text(self):
        if (self.kind == "audio") != (self.text is not None):
            raise ValueError("audio resources carry exactly the text Jane says; others none")
        if (self.stage == "story") != (self.paragraph is not None):
            raise ValueError("a story picture names its paragraph; others none")
        if (self.stage == "option") != (self.option is not None):
            raise ValueError("an option picture names its option; others none")
        if self.picture_question and (self.stage != "question" or self.kind != "image"):
            raise ValueError("a question picture is a still picture shown with the question")
        return self


class StoryLevel(Contract):
    """The Lexile a story aims at and its word range; a target, never a measurement."""

    lexile: str
    challenge: Challenge | None  # None: the placement lesson adds no step.
    target: str
    words: tuple[int, int]


class CourseBody(Contract):
    status: Literal["checked"] = "checked"
    demand_ref: RevisionRef
    state_ref: RevisionRef
    plan_ref: RevisionRef
    jane_ref: RevisionRef
    brief_ref: RevisionRef
    material_refs: tuple[RevisionRef, ...]
    primary_goal: Goal
    auxiliary_goal: Goal
    budget: dict[str, int]
    lesson_type: Literal["observation", "practice", "review"]
    tasks: tuple[TaskDefinition, ...] = Field(min_length=1)
    demand_mapping: tuple[RequirementMapping, ...] = Field(min_length=1)
    parent_preview: dict[str, str]
    # Jane's opening reading tip (ADR-0022); courses saved before it have none.
    reading_focus: str | None = None
    story_level: StoryLevel | None = None  # None: no Lexile, or an older course.
    vocabulary_used: tuple[str, ...] = ()  # Marked words the story reviews (design/learning).
    # One sentence on how this lesson's pictures look (ADR-0026); None: no pictures or older.
    picture_style: str | None = None
    limitations: tuple[str, ...]
    resources: tuple[ResourceSpec, ...] = ()

    @model_validator(mode="after")
    def consistent_package(self):
        # Courses saved before ADR-0020 sum to 30; estimated ones stay within 40.
        if sum(self.budget.values()) > MAX_MINUTES or any(v <= 0 for v in self.budget.values()):
            raise ValueError("the entire course budget must stay within 40 minutes")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("task definitions need distinct identities")
        if any(task.material_ref not in self.material_refs for task in self.tasks):
            raise ValueError("task material must belong to the fixed package")
        task_ids = {task.task_id for task in self.tasks}
        if any(set(item.task_ids) - task_ids for item in self.demand_mapping):
            raise ValueError("demand mapping must point to tasks of this package")
        if any(set(item.task_ids) - task_ids for item in self.resources):
            raise ValueError("resources must point to tasks of this package")
        return self
