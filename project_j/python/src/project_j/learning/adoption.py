from datetime import datetime

from project_j.contracts import Document, RevisionRef
from project_j.learning.contracts import (
    DemandBody,
    DimensionState,
    LearningState,
    StateBody,
    SuggestionChoice,
)

HELP_ORDER = ("none", "light_prompt", "scaffold", "full_model")


def adopt_state(current: LearningState, evaluation: Document) -> StateBody:
    dimensions = {item.dimension_id: item for item in current.body.dimensions}
    observed: dict[str, list[dict]] = {}
    for task in evaluation.body["tasks"]:
        observed.setdefault(task["dimension_id"], []).append(task)
    # All tasks of one dimension are merged; their order in the lesson never decides the result.
    for dimension_id, tasks in observed.items():
        previous = dimensions.get(dimension_id)
        supported = [task for task in tasks if task["working_judgment"] != "unknown"]
        judgments = {task["working_judgment"] for task in supported}
        judgment = (
            "unknown"
            if not judgments
            else judgments.pop()
            if len(judgments) == 1
            else "inconsistent"
        )
        levels = [task["help"].get(dimension_id, "unknown") for task in supported]
        known = [level for level in levels if level in HELP_ORDER]
        level = (
            max(known, key=HELP_ORDER.index) if known and len(known) == len(levels) else "unknown"
        )
        names = "、".join(sorted({task["task_id"] for task in tasks}))
        dimensions[dimension_id] = DimensionState(
            dimension_id=dimension_id,
            task_scope=f"模拟任务{names}及当前材料条件",
            judgment=judgment,
            evidence_refs=(evaluation.ref,),
            help=level,
            material_condition="当时固定课程的模拟使用条件",
            observed_at=evaluation.occurred_at,
            retention=previous.retention if previous else None,
            transfer=previous.transfer if previous else None,
            limitations=tuple(sorted({item for task in tasks for item in task["limitations"]}))
            + ("不因当堂正确填独立或长期掌握。",),
        )
    return StateBody(status="checked", dimensions=tuple(dimensions.values()))


def adopt_demand(
    current: DemandBody, state_ref: RevisionRef, plan_ref: RevisionRef, suggestion: Document
) -> DemandBody:
    return DemandBody(
        **(
            current.model_dump()
            | {
                "status": "checked",
                "state_ref": state_ref,
                "plan_ref": plan_ref,
                "sampling_requirements": tuple(suggestion.body["sampling"]),
                "help_requirements": tuple(suggestion.body["help"]),
                "suggestion_choices": (
                    SuggestionChoice(
                        suggestion_ref=suggestion.ref,
                        action="adopt",
                        reason="在既定目标与30分钟预算内采用新材料短观察，保留帮助与未知。",
                    ),
                ),
                "gaps": ("保持、迁移、外部帮助与真实声音条件仍待观察。",),
            }
        )
    )


def review_due(plans: list[Document], now: datetime) -> list[dict]:
    result = []
    for plan in plans:
        review_at = plan.body.get("review_at")
        if review_at and datetime.fromisoformat(review_at.replace("Z", "+00:00")) <= now:
            result.append(
                {
                    "plan_ref": plan.ref.model_dump(),
                    "level": plan.body["level"],
                    "state": "due",
                    "reason": "到期只形成待复盘事项，不是完成或能力进度。",
                }
            )
    return result
