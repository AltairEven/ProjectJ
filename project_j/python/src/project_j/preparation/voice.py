"""Jane's fixed sentences: the words the classroom says and the audio prepared for them.

Preparation and the classroom share these texts, so prepared audio matches what is said.
"""

from project_j.preparation.contracts import (
    MAX_AUDIO_CHARS,
    CourseBody,
    ResourceSpec,
    TaskDefinition,
)
from project_j.preparation.task_types import PROGRAM_JUDGED

__all__ = [
    "MAX_AUDIO_CHARS",
    "WRONG_TEXT",
    "audio_resources",
    "opening_text",
    "question_speech",
    "question_text",
]
# What Jane says first when a clicked task is answered wrong; that level's help follows.
WRONG_TEXT = "Not yet."


def opening_text(course: CourseBody) -> str:
    """Jane's opening: what to notice while reading, then how to start (ADR-0022)."""
    again = " You have read this story before." if course.lesson_type == "review" else ""
    focus = course.reading_focus or "Read the story on your own."
    return f"I'm Jane.{again} {focus} Press Start reading when you are ready."


def question_text(task: TaskDefinition, budget: dict[str, int]) -> str:
    """What the question shows."""
    if task.purpose == "offscreen":
        # The offscreen action itself carries its minutes and the way back; a course saved
        # before ADR-0020 has them in its budget.
        minutes = task.minutes or budget["offscreen"]
        return (
            f"{task.prompt} This takes about {minutes} minutes. When you are done, "
            'press "Back" and tell me what really happened.'
        )
    if task.purpose == "read_aloud":
        return f"{task.prompt}\n{task.reference_text}"
    return task.prompt


def question_speech(task: TaskDefinition, budget: dict[str, int]) -> str:
    """What Jane says for it: never the passage the child is about to read."""
    return task.prompt if task.purpose == "read_aloud" else question_text(task, budget)


def audio_resources(course: CourseBody) -> tuple[ResourceSpec, ...]:
    def statement(resource_id, purpose, text, task_ids=()):
        return ResourceSpec(
            resource_id=resource_id,
            kind="audio",
            purpose=purpose,
            content="Made ahead from the course text and played in class",
            task_ids=task_ids,
            audience="student",
            required=False,
            fallback="If not adopted, the same sentence is spoken live",
            text=text,
        )

    resources = [statement("voice:goal", "Jane's fixed line: opening", opening_text(course))]
    for task in course.tasks:
        # Too long for one synthesis request: nothing to prepare; live speech says so in class.
        if len(question_speech(task, course.budget)) > MAX_AUDIO_CHARS:
            continue
        resources.append(
            statement(
                f"voice:{task.task_id}",
                f"Jane's fixed line: question {task.task_id}",
                question_speech(task, course.budget),
                (task.task_id,),
            )
        )
    for task in course.tasks:
        if task.purpose in PROGRAM_JUDGED:  # Every line Jane may say on a clicked task.
            lines = [("correct", "when right", task.correct_text)]
            for level, support in enumerate(task.supports, 1):
                lines += [
                    (
                        f"wrong-{level}",
                        f"help {level} after a wrong answer",
                        f"{WRONG_TEXT} {support.text}",
                    ),
                    (f"hint-{level}", f"help {level} on a hint", support.text),
                ]
            lines.append(("exit", "exit line", task.exit_text))
            resources += [
                statement(
                    f"voice:{task.task_id}:{name}",
                    f"Jane's fixed line: {task.task_id}, {what}",
                    text,
                    (task.task_id,),
                )
                for name, what, text in lines
                if len(text) <= MAX_AUDIO_CHARS
            ]
    for task in course.tasks:
        if task.purpose == "read_aloud":
            resources.append(
                ResourceSpec(
                    resource_id=f"demo:{task.task_id}",
                    kind="audio",
                    purpose=f"Reading model: {task.task_id}",
                    content="Jane reads this part aloud as the second help",
                    task_ids=(task.task_id,),
                    audience="student",
                    required=True,
                    fallback="The class starts only after the model reading is adopted; "
                    "live speech never stands in for it",
                    stage="help",
                    text=task.reference_text,
                )
            )
    return tuple(resources)
