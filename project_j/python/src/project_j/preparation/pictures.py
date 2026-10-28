"""Story pictures and picture questions of a drafted lesson (ADR-0026).

The rules count, place and attach them; whether a picture is drawn right is checked later by
the preparation model looking at it (preparation/resources.py).
"""

from collections import Counter

MAX_ILLUSTRATIONS = 2
MAX_PICTURE_TASKS = 2
OPTION_PICTURES = (3, 4)


def task_picture(item: dict) -> bool:
    """A picture that is part of its question: an option picture or a question picture."""
    return item.get("stage") == "option" or bool(item.get("picture_question"))


def picture_groups(resources: list[dict]) -> list[tuple[str, list[str]]]:
    """The pictures checked together: every picture of one picture question, or one still
    story picture; animations are not looked at."""
    groups: dict[str, list[str]] = {}
    for item in resources:
        if item.get("stage") == "story" and item["kind"] == "image":
            groups[f"story:{item['resource_id']}"] = [item["resource_id"]]
        elif task_picture(item):
            groups.setdefault(f"task:{item['task_ids'][0]}", []).append(item["resource_id"])
    return list(groups.items())


def picture_problems(draft, paragraphs: int) -> list[tuple[str, str]]:
    """What the preparation model must repair in the draft's pictures; [] when none."""
    found: list[str] = []
    tasks = {task.task_id: task for task in draft.tasks}
    resources = draft.resources
    for item in resources:
        rid = item.resource_id
        if item.paragraph is not None and item.stage != "story":
            found.append(f"{rid}: only a story picture names a paragraph")
        if item.option is not None and item.stage != "option":
            found.append(f"{rid}: only an option picture names an option")
        if item.option is None and item.stage == "option":
            found.append(f"{rid}: an option picture names its option")
        if item.picture_question and item.stage != "question":
            found.append(f"{rid}: only a question picture is marked picture_question")

    story = [item for item in resources if item.stage == "story"]
    if len(story) > MAX_ILLUSTRATIONS:
        found.append(f"Use at most {MAX_ILLUSTRATIONS} story pictures")
    for item in story:
        if item.paragraph is None or not 1 <= item.paragraph <= paragraphs:
            found.append(f"{item.resource_id}: the paragraph must be 1 to {paragraphs}")
        if (
            item.task_ids
            or item.audience != "student"
            or item.required
            or item.kind not in {"image", "animation"}
        ):
            found.append(
                f"{item.resource_id}: a story picture has no task, is for the student, is "
                "optional, and is an image or animation"
            )
    for paragraph, count in Counter(item.paragraph for item in story).items():
        if paragraph is not None and count > 1:
            found.append(f"Paragraph {paragraph} has more than one story picture")

    pictured: dict[str, list] = {}
    for item in resources:
        if item.stage != "option" and not (item.stage == "question" and item.picture_question):
            continue
        if (
            item.kind != "image"
            or item.audience != "student"
            or item.required
            or len(item.task_ids) != 1
        ):
            found.append(
                f"{item.resource_id}: a question picture is an optional still picture for the "
                "student, for exactly one question"
            )
            continue
        task = tasks.get(item.task_ids[0])
        if task is None:
            continue  # The resource checks report a missing task.
        if item.stage == "option" and task.purpose != "choice":
            found.append(f"{item.resource_id}: option pictures belong to a choice question")
        elif item.stage == "question" and task.purpose not in {"choice", "fill"}:
            found.append(
                f"{item.resource_id}: a question picture belongs to a choice or fill-in question"
            )
        else:
            pictured.setdefault(task.task_id, []).append(item)
    if len(pictured) > MAX_PICTURE_TASKS:
        found.append(f"Use pictures in at most {MAX_PICTURE_TASKS} questions")
    for task_id, items in pictured.items():
        options = [
            item.option for item in items if item.stage == "option" and item.option is not None
        ]
        looks = [item for item in items if item.stage == "question"]
        shown = [
            item for item in resources if item.stage == "question" and task_id in item.task_ids
        ]
        if options and shown:
            found.append(f"{task_id}: use picture options or a question picture, not both")
        if len(looks) > 1:
            found.append(f"{task_id}: one question picture per question")
        if options:
            choice = tasks[task_id].choice
            low, high = OPTION_PICTURES
            if (
                choice is None
                or choice.variant != "single"
                or not low <= len(choice.options) <= high
            ):
                found.append(
                    f"{task_id}: picture options need a single choice with {low} or {high} options"
                )
            elif sorted(options) != list(range(len(choice.options))):
                found.append(f"{task_id}: give exactly one option picture for each option")

    if not draft.picture_style and any(item.kind in {"image", "animation"} for item in resources):
        found.append("Write picture_style for the lesson's pictures")
    return [("picture", text) for text in dict.fromkeys(found)]
