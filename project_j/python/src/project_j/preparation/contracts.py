from typing import Literal

from pydantic import Field, model_validator

from project_j.contracts import Contract, RevisionRef
from project_j.learning.contracts import DemandBody, Goal, Help


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
    purpose: Literal["answer", "point", "offscreen"]
    prompt: str = Field(min_length=1)
    material_ref: RevisionRef
    evidence: str = Field(min_length=1)
    acceptable_simulated: tuple[str, ...]
    incorrect_simulated: tuple[str, ...] = ()
    supports: tuple[Support, ...] = Field(min_length=2)
    exit_text: str = Field(min_length=1)
    optional: bool = False

    @model_validator(mode="after")
    def different_supports(self):
        if len({support.text for support in self.supports}) != len(self.supports):
            raise ValueError("help branches must provide different actual guidance")
        return self


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
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def consistent_package(self):
        if sum(self.budget.values()) != 30 or any(value <= 0 for value in self.budget.values()):
            raise ValueError("the entire course budget must stay within the fixed 30 minutes")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("task definitions need distinct identities")
        if any(task.material_ref not in self.material_refs for task in self.tasks):
            raise ValueError("task material must belong to the fixed package")
        task_ids = {task.task_id for task in self.tasks}
        if any(set(item.task_ids) - task_ids for item in self.demand_mapping):
            raise ValueError("demand mapping must point to tasks of this package")
        return self
