from datetime import datetime

from project_j.contracts import Document
from project_j.learning.contracts import DimensionState, StateBody

HELP_ORDER = ("none", "light_prompt", "scaffold", "full_model")


def adopt_state(current: Document, evaluation: Document) -> StateBody:
    """current: the stored state document; its body is checked as a StateBody here."""
    body = StateBody.model_validate(current.body)
    dimensions = {item.dimension_id: item for item in body.dimensions}
    observed: dict[str, list[dict]] = {}
    for task in evaluation.body["tasks"]:
        # Actual help names every dimension it affected (an English sentence frame on a reading
        # task also samples English expression); dimensions without such help are not inferred.
        affected = {dim for dim, level in task["help"].items() if level not in {"none", "unknown"}}
        for dimension_id in {task["dimension_id"], *affected}:
            observed.setdefault(dimension_id, []).append(task)
    # All tasks of one dimension are merged; their order in the lesson never decides the result.
    for dimension_id, tasks in observed.items():
        previous = dimensions.get(dimension_id)
        if previous and previous.observed_at and previous.observed_at > evaluation.occurred_at:
            # Re-reviewing an older lesson's corrected source never overwrites newer evidence.
            activity = evaluation.body["activity_id"]
            note = (
                f"An earlier class ({activity}) was corrected and checked again; "
                "newer evidence counts."
            )
            if note not in previous.limitations:
                previous = previous.model_copy(
                    update={"limitations": (*previous.limitations, note)}
                )
            dimensions[dimension_id] = previous
            continue
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
        names = ", ".join(sorted({task["task_id"] for task in tasks}))
        # The stored state carries the partition; a just-made evaluation is not labelled yet.
        source = "" if current.realm == "study" else "simulated "
        dimensions[dimension_id] = DimensionState(
            dimension_id=dimension_id,
            task_scope=f"{source}tasks {names} with the current material",
            judgment=judgment,
            evidence_refs=(evaluation.ref,),
            help=level,
            material_condition=f"the {source}conditions of that lesson",
            observed_at=evaluation.occurred_at,
            retention=previous.retention if previous else None,
            transfer=previous.transfer if previous else None,
            limitations=tuple(sorted({item for task in tasks for item in task["limitations"]}))
            + ("Right in one class does not mean learned for good.",),
        )
    return StateBody(status="checked", dimensions=tuple(dimensions.values()))


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
                    "reason": "A due date only means a review is due, not that anything is done.",
                }
            )
    return result
