from datetime import datetime

from project_j.contracts import Document, RevisionRef
from project_j.learning.contracts import (
    DemandBody,
    DimensionState,
    LearningState,
    StateBody,
    SuggestionChoice,
)


def adopt_state(current: LearningState, evaluation: Document) -> StateBody:
    dimensions = {item.dimension_id: item for item in current.body.dimensions}
    for task in evaluation.body["tasks"]:
        previous = dimensions.get(task["dimension_id"])
        judgment = task["working_judgment"]
        level = task["help"].get(task["dimension_id"], "unknown")
        dimensions[task["dimension_id"]] = DimensionState(
            dimension_id=task["dimension_id"],
            task_scope=f"模拟任务{task['task_id']}及当前材料条件",
            judgment=judgment,
            evidence_refs=(evaluation.ref,),
            help=level,
            material_condition="当时固定课程的模拟使用条件",
            observed_at=evaluation.occurred_at,
            retention=previous.retention if previous else None,
            transfer=previous.transfer if previous else None,
            limitations=tuple(task["limitations"]) + ("不因当堂正确填独立或长期掌握。",),
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
