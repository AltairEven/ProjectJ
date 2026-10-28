"""What the classroom registers for each clicked task type: what the page shows, how an answer
is judged and how it reads for the evaluation (ADR-0020). No model is called for these types.

The page sees shuffled options, cards, right-hand items and word banks and answers by display
position; the answer itself never reaches the page.
"""

import random
from collections.abc import Callable
from hashlib import sha256

from project_j.english import ENGLISH_ONLY, han
from project_j.preparation.contracts import TaskDefinition
from project_j.preparation.task_types import BLANK, BLANKS, PROGRAM_JUDGED

MALFORMED = "That answer doesn't fit this question. Please try again."


def shuffled(task_id: str, count: int) -> tuple[int, ...]:
    """Display position -> original index; stable per task, never the original order."""
    order = list(range(count))
    random.Random(int.from_bytes(sha256(task_id.encode()).digest()[:8])).shuffle(order)
    if count > 1 and order == list(range(count)):
        order = order[-1:] + order[:-1]
    return tuple(order)


def order_of(task: TaskDefinition) -> tuple[int, ...]:
    """Display position -> original index of what the page lists for this task."""
    if task.purpose == "choice":  # True/false keeps its fixed order.
        count = len(task.choice.options)
        return (
            shuffled(task.task_id, count)
            if task.choice.variant == "single"
            else tuple(range(count))
        )
    if task.purpose == "order":
        return shuffled(task.task_id, len(task.order.items))
    if task.purpose == "match":
        return shuffled(task.task_id + ":right", len(task.match.right + task.match.distractors))
    words = len(task.fill.answers) + len(task.fill.distractors)
    return shuffled(task.task_id + ":bank", words)


def listed(task: TaskDefinition) -> list[str]:
    """What the page lists, in display order: options, cards, the right column or a word bank."""
    items = {
        "choice": lambda: task.choice.options,
        "order": lambda: task.order.items,
        "match": lambda: task.match.right + task.match.distractors,
        "fill": lambda: (
            tuple(spellings[0] for spellings in task.fill.answers) + task.fill.distractors
        ),
    }[task.purpose]()
    return [items[i] for i in order_of(task)]


def option_pictures(task: TaskDefinition, visible) -> list[str] | None:
    """The adopted option pictures of a choice task in display order, when every option has
    one (ADR-0026); None otherwise, and the page shows the options as words."""
    if task.purpose != "choice":
        return None
    by_option = {
        item.option: item.resource_id
        for item in visible
        if item.stage == "option" and task.task_id in item.task_ids
    }
    if set(by_option) != set(range(len(task.choice.options))):
        return None
    return [by_option[index] for index in order_of(task)]


def presentation(task: TaskDefinition) -> dict | None:
    """The question as the page shows it; None for a task answered by text or recording."""
    if task.purpose not in PROGRAM_JUDGED:
        return None
    view = {"type": task.purpose, "version": task.type_version}
    if task.purpose == "choice":
        return view | {"variant": task.choice.variant, "options": listed(task)}
    if task.purpose == "order":
        return view | {"variant": task.order.variant, "items": listed(task)}
    if task.purpose == "match":
        return view | {"left": list(task.match.left), "right": listed(task)}
    item = task.fill
    return view | {
        "variant": item.variant,
        "text": BLANKS.sub(BLANK, item.text),
        "blanks": len(item.answers),
        "bank": listed(task) if item.variant == "word_bank" else None,
    }


def positions(response: dict, key: str, count: int, length: int, distinct=True) -> list[int]:
    """Display positions below count, of the given length; anything else is malformed."""
    value = response.get(key)
    if (
        set(response) != {key}
        or not isinstance(value, list)
        or len(value) != length
        or any(type(item) is not int or not 0 <= item < count for item in value)
        or (distinct and len(set(value)) != len(value))
    ):
        raise ValueError(MALFORMED)
    return value


def judge_choice(task: TaskDefinition, response: dict) -> bool:
    count = len(task.choice.options)
    (picked,) = positions({"choice": [response.get("choice")]}, "choice", count, 1)
    if set(response) != {"choice"}:
        raise ValueError(MALFORMED)
    return order_of(task)[picked] == task.choice.correct


def judge_order(task: TaskDefinition, response: dict) -> bool:
    count = len(task.order.items)
    order = positions(response, "order", count, count)
    return [order_of(task)[i] for i in order] == list(range(count))


def judge_match(task: TaskDefinition, response: dict) -> bool:
    count = len(task.match.right + task.match.distractors)
    pairs = positions(response, "pairs", count, len(task.match.left))
    return [order_of(task)[i] for i in pairs] == list(range(len(task.match.left)))


def normal(text: str) -> str:
    """Case, outer spaces, runs of spaces and end punctuation do not make a fill-in wrong."""
    return " ".join(text.split()).casefold().rstrip(".!?,;:。！？，；：").strip()


def judge_fill(task: TaskDefinition, response: dict) -> bool:
    value = response.get("blanks")
    answers = task.fill.answers
    if (
        set(response) != {"blanks"}
        or not isinstance(value, list)
        or len(value) != len(answers)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(MALFORMED)
    if any(han(item) for item in value):  # Not counted as wrong: answer again in English.
        raise ValueError(ENGLISH_ONLY)
    return all(
        normal(given) in {normal(spelling) for spelling in spellings}
        for given, spellings in zip(value, answers, strict=True)
    )


JUDGES: dict[str, Callable[[TaskDefinition, dict], bool]] = {
    "choice": judge_choice,
    "order": judge_order,
    "match": judge_match,
    "fill": judge_fill,
}


def judge(task: TaskDefinition, response: dict) -> bool:
    """Right or wrong by the task's own rule; a malformed response raises ValueError."""
    if not isinstance(response, dict):
        raise ValueError(MALFORMED)
    return JUDGES[task.purpose](task, response)


def describe(task: TaskDefinition, response: dict) -> str:
    """The answer as the evaluation reads it: what the student actually picked or wrote."""
    if task.purpose == "fill":
        return " / ".join(response["blanks"])
    shown = listed(task)
    if task.purpose == "choice":
        return shown[response["choice"]]
    if task.purpose == "order":
        return " → ".join(shown[i] for i in response["order"])
    pairs = zip(task.match.left, response["pairs"], strict=True)
    return "; ".join(f"{left} = {shown[i]}" for left, i in pairs)
