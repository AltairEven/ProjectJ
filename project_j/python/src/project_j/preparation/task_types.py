"""What preparation registers for each task type (ADR-0020).

One type is one code, the purpose of a course task. Preparation registers here its content,
the checks of that content, the minutes it takes, the instruction for the preparation model and
its difficulty ladder; the classroom registers what it shows and how it judges
(classroom/responses.py), evaluation how it scores (evaluation/scoring.py), the page its widget
(web/src/tasks.ts).
"""

import re
from math import ceil
from typing import Literal

from project_j.contracts import Contract
from project_j.learning.contracts import Text
from project_j.preparation.reading import flat, words

# Judged by the classroom's own rules, answered by clicking or filling, never by a model.
PROGRAM_JUDGED = frozenset({"choice", "order", "match", "fill"})
# Types whose content sits in the course field of the same name.
CONTENT_TYPES = PROGRAM_JUDGED | {"open"}
BLANK = "___"
BLANKS = re.compile(r"_{3,}")
TRUE_FALSE = ("对", "错")
NOT_GIVEN = "文中没提"
TRUE_FALSE_NOT_GIVEN = ("对", "错", NOT_GIVEN)
MAX_MINUTES, MIN_PRACTICE_MINUTES = 40, 20


class ChoiceContent(Contract):
    variant: Literal["true_false", "true_false_not_given", "single"]
    options: tuple[Text, ...]
    correct: int


class OrderContent(Contract):
    variant: Literal["events", "words"]
    items: tuple[Text, ...]  # In the right order.


class MatchContent(Contract):
    left: tuple[Text, ...]
    right: tuple[Text, ...]  # right[i] belongs to left[i].
    distractors: tuple[Text, ...]


class FillContent(Contract):
    variant: Literal["word_bank", "free"]
    text: Text  # One sentence with ___ for each blank.
    answers: tuple[tuple[Text, ...], ...]  # Per blank, the accepted spellings.
    distractors: tuple[Text, ...]


class OpenContent(Contract):
    variant: Literal["retell", "opinion", "what_if"]
    points: tuple[Text, ...]
    guidance: Text


def content(task):
    """The task's own content: the field named after its type, or None."""
    return getattr(task, task.purpose) if task.purpose in CONTENT_TYPES else None


# Initial estimates in minutes (my estimate, with Jane's question, the answer and on average one
# help); QT-4 calibrates them from sealed classes.
FIXED_MINUTES = {"answer": 2, "point": 2, "open": 4}
CONTENT_MINUTES = {
    "choice": lambda item: 0.5 if item.variant == "true_false" else 1,
    "order": lambda item: 0.5 + 0.25 * len(item.items),
    "match": lambda item: 0.5 + 0.25 * len(item.left),
    "fill": lambda item: 0.5 + 0.5 * len(item.answers),
}


def task_minutes(task) -> float:
    if task.purpose in CONTENT_MINUTES:
        return CONTENT_MINUTES[task.purpose](content(task))
    if task.purpose == "read_aloud":
        return 1 + len(words(task.reference_text)) / 60
    if task.purpose == "offscreen":
        return task.minutes or 0  # A course saved before QT-1 has its minutes in the budget.
    return FIXED_MINUTES[task.purpose]


def course_budget(tasks, story: str, factors: dict[str, float] | None = None) -> dict[str, int]:
    """The course's estimate by part, each part rounded up; offscreen minutes are never scaled."""
    factors = factors or {}
    sums: dict[str, float] = {}
    for task in tasks:
        part = task.purpose if task.purpose in {"read_aloud", "offscreen"} else "questions"
        factor = 1 if task.purpose == "offscreen" else factors.get(task.purpose, 1)
        sums[part] = sums.get(part, 0) + task_minutes(task) * factor
    return (
        {"opening": 2, "reading": up(1 + len(words(story)) / 60)}
        | {
            part: up(sums[part])
            for part in ("questions", "read_aloud", "offscreen")
            if part in sums
        }
        | {"closing": 2}
    )


def up(minutes: float) -> int:
    return ceil(round(minutes, 6))


def quoted(text: str, story: str) -> bool:
    return flat(text) in flat(story)


def filled(item: FillContent) -> str:
    """The sentence with every blank filled by its first accepted spelling."""
    parts = BLANKS.split(item.text)
    answers = [spellings[0] if spellings else "" for spellings in item.answers]
    return "".join(part + (answers[i] if i < len(answers) else "") for i, part in enumerate(parts))


def content_problems(task_id: str, purpose: str, item, story: str) -> list[tuple[str, str]]:
    """Whether a task's content fits its type; problems the preparation model can repair."""
    found = []

    def problem(text):
        found.append(("content", f"{task_id}{text}"))

    if purpose == "choice":
        fixed = {"true_false": TRUE_FALSE, "true_false_not_given": TRUE_FALSE_NOT_GIVEN}.get(
            item.variant
        )
        if fixed and item.options != fixed:
            problem(f"的选项须固定为{'、'.join(fixed)}")
        if not fixed and not (2 <= len(item.options) <= 5 and distinct(item.options)):
            problem("须有2到5个互不相同的选项")
        if not 0 <= item.correct < len(item.options):
            problem("的正确项不在选项里")
    elif purpose == "order":
        if not (3 <= len(item.items) <= 6 and distinct(item.items)):
            problem("须有3到6张互不相同的卡片")
        if item.variant == "words" and not quoted(" ".join(item.items), story):
            problem("连起来不是故事原句")
    elif purpose == "match":
        if not (
            3 <= len(item.left) <= 6
            and len(item.right) == len(item.left)
            and len(item.distractors) <= 2
            and distinct(item.left)
            and distinct(item.right + item.distractors)
        ):
            problem("须左右各3到6项一一对应、0到2个干扰项且互不相同")
    elif purpose == "fill":
        if not item.answers or len(BLANKS.findall(item.text)) != len(item.answers):
            problem("的空格数须与答案数相同且至少一个")
        elif any(not 1 <= len(spellings) <= 3 for spellings in item.answers):
            problem("每空须有1到3种可接受写法")
        elif not quoted(filled(item), story):
            problem("填回答案后不是故事原句")
        if item.variant == "word_bank" and not 1 <= len(item.distractors) <= 3:
            problem("选词填空须有1到3个干扰词")
        if item.variant == "free" and item.distractors:
            problem("自由填空不给干扰词")
    elif purpose == "open":
        if not (1 <= len(item.points) <= 5 and distinct(item.points)):
            problem("须有1到5条互不相同的要点")
    return found


def distinct(items: tuple[str, ...]) -> bool:
    return len({flat(item).casefold() for item in items}) == len(items)


def shown_text(purpose: str, item) -> tuple[str, ...]:
    """What the student sees of a clicked task, piece by piece."""
    if purpose == "choice":
        return item.options
    if purpose == "order":
        return item.items
    if purpose == "match":
        return item.left + item.right + item.distractors
    if purpose == "fill":
        bank = tuple(spellings[0] for spellings in item.answers if spellings)
        return (item.text, *(bank + item.distractors if item.variant == "word_bank" else ()))
    return ()


def plain(text: str) -> str:
    """Case, spacing and punctuation folded away, for comparing what is shown."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


PROGRAM_INSTRUCTIONS = (
    "choice、order、match、fill由程序对照答案判断，课上学生只能点选或填写、不能提问："
    "dimension_id为english.reading.facts，evidence逐字摘自故事，acceptable_answers与known_incorrect为空，"
    "correct_text写答对时Jane说的一句话；各级帮助在学生答错或点“给我提示”时由Jane照原文说出，"
    "不能直接说出答案。"
)
TYPE_INSTRUCTIONS = {
    "choice": 'choice是选择题：variant为true_false（options固定为["对","错"]）、'
    'true_false_not_given（options固定为["对","错","文中没提"]）或single（2到5个选项），'
    "correct是正确项在options里的序号（从0起），判断题把要判断的那句话写进prompt。",
    "order": "order是排序题：variant为events（故事事件）或words"
    "（连词成句，items连起来须是故事原句），items按正确顺序写3到6项。",
    "match": "match是配对题：left与right各3到6项，right[i]配left[i]，"
    "distractors是0到2个右列干扰项。",
    "fill": "fill是填空题：text是用___标出空格的一句，每空第一种写法填回后须是故事原句；"
    "answers每空1到3种可接受写法；variant为word_bank时distractors写1到3个干扰词，free时为空。",
    "open": "open是开放问答：open字段填variant（retell复述、opinion谈看法、what_if“如果换成你”）、"
    "points（1到5条好回答应覆盖的要点，复述的要点取自故事情节）与guidance（怎样算答得够）；"
    "dimension_id为english.speaking.report或english.reading.facts；evidence逐字摘自它所依据的故事句子；"
    "acceptable_answers与known_incorrect为空，correct_text为null。规则会把朗读题放在第一道open题之前。",
}
CONTENT_FIELDS_INSTRUCTION = (
    "每题只填与purpose同名的内容字段（choice、order、match、fill、open），其余内容字段为null；"
    "只有choice、order、match、fill写correct_text，其他题型correct_text为null。"
)
# Variants from easy to hard; the preparation model moves along them by the recent scores.
LADDERS = {
    "choice": "判断、单选、对错没提",
    "order": "3张卡到5张卡",
    "match": "4对到6对加干扰项",
    "fill": "选词到自由填写",
    "open": "复述到谈看法、如果换成你",
}


def ladder_text() -> str:
    return "；".join(f"{code}为{ladder}" for code, ladder in LADDERS.items())


# Calibration (QT-4): what each sealed task actually took against its estimate.
CALIBRATION_WINDOW, CALIBRATION_MINIMUM = 20, 5
RATIO_BOUNDS = (0.3, 3.0)


def timing_samples(fact: dict, course) -> list[tuple[str, float, float]]:
    """(purpose, actual minutes, estimated minutes) per task instance, from its question to its
    last judgment; facts sealed before QT-1 have no times and give none."""
    tasks = {task.task_id: task for task in course.tasks}
    instances: dict[str, list[dict]] = {}
    for attempt in fact.get("attempts", []):
        instances.setdefault(attempt["task_instance_id"], []).append(attempt)
    samples = []
    for attempts in instances.values():
        task = tasks.get(attempts[0]["task_id"])
        started = attempts[0].get("task_started_ms")
        judged = [a["judged_ms"] for a in attempts if a.get("judged_ms") is not None]
        if task is None or task.purpose == "offscreen" or started is None or not judged:
            continue
        estimate = task_minutes(task)
        if estimate > 0:
            samples.append((task.purpose, (max(judged) - started) / 60000, estimate))
    return samples


def calibration(samples: list[tuple[str, float, float]]) -> dict[str, float]:
    """Per type, the mean of actual/estimate over its latest samples (oldest first in samples);
    one long pause cannot swing it, each ratio is bounded first."""
    ratios: dict[str, list[float]] = {}
    for purpose, actual, estimate in samples:
        ratios.setdefault(purpose, []).append(
            min(max(actual / estimate, RATIO_BOUNDS[0]), RATIO_BOUNDS[1])
        )
    return {
        purpose: round(sum(latest) / len(latest), 2)
        for purpose, values in ratios.items()
        if len(latest := values[-CALIBRATION_WINDOW:]) >= CALIBRATION_MINIMUM
    }
