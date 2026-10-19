"""Jane's fixed sentences: the words the classroom says and the audio prepared for them.

Preparation and the classroom share these texts, so prepared audio matches what is said.
"""

from project_j.preparation.contracts import (
    MAX_AUDIO_CHARS,
    CourseBody,
    ResourceSpec,
    TaskDefinition,
)

__all__ = ["MAX_AUDIO_CHARS", "audio_resources", "goal_text", "question_speech", "question_text"]


def goal_text(course: CourseBody) -> str:
    reading = "先复习读过的故事" if course.lesson_type == "review" else "先读故事"
    observe = (
        "，再做离屏短观察" if any(task.purpose == "offscreen" for task in course.tasks) else ""
    )
    return f"我是Jane。{reading}、找依据{observe}；可随时暂停或停止。请确认收到安排。"


def question_text(task: TaskDefinition, budget: dict[str, int]) -> str:
    """What the question shows."""
    if task.purpose == "offscreen":
        # The offscreen action itself carries its budget and the way back.
        return f"{task.prompt}这一段约{budget['offscreen']}分钟；做完点“返回”，再说实际结果。"
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
            content="按课程文字预合成，课堂直接播放",
            task_ids=task_ids,
            audience="student",
            required=False,
            fallback="未采用时课堂现场合成同一句",
            text=text,
        )

    resources = [statement("voice:goal", "Jane的固定语句：开场说明", goal_text(course))]
    for task in course.tasks:
        # Too long for one synthesis request: nothing to prepare; live speech says so in class.
        if len(question_speech(task, course.budget)) > MAX_AUDIO_CHARS:
            continue
        resources.append(
            statement(
                f"voice:{task.task_id}",
                f"Jane的固定语句：{task.task_id}题目",
                question_speech(task, course.budget),
                (task.task_id,),
            )
        )
    for task in course.tasks:
        if task.purpose == "read_aloud":
            resources.append(
                ResourceSpec(
                    resource_id=f"demo:{task.task_id}",
                    kind="audio",
                    purpose=f"朗读示范：{task.task_id}",
                    content="Jane示范朗读本段，作为第二级帮助",
                    task_ids=(task.task_id,),
                    audience="student",
                    required=True,
                    fallback="采用示范音后才能开课；不现场合成冒充示范",
                    stage="help",
                    text=task.reference_text,
                )
            )
    return tuple(resources)
