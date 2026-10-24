"""Requests for the pictures and animations a confirmed course lists (ADR-0017).

Only the resource's own description and the questions it accompanies go to the model:
no learner identity, no answers.
"""

from project_j.contracts import Contract
from project_j.platform.models.contracts import ImageNote, ModelRequest
from project_j.preparation.contracts import CourseBody

IMAGE_INSTRUCTIONS = (
    "为学生的课堂画一张教学插图。按purpose与content画，画面简洁清楚，"
    "除非content要求不加文字；不画出questions的答案，不画真实人物或品牌；"
    "画完后在description里用一句简单英文说明画了什么。"
)
ANIMATION_INSTRUCTIONS = (
    "为学生的课堂写一段会动的SVG动画。按purpose与content画，"
    "只用SVG绘图元素与animate、animateTransform、animateMotion、set等SMIL动画元素，"
    "根元素为svg并带viewBox，循环播放；不用script、style元素、foreignObject、image、"
    "外部链接或事件属性；不演示questions的答案。description用一句简单英文说明动画内容。"
)


class AnimationDraft(Contract):
    svg: str
    description: str


def material_context(kind: str, item: dict, course: CourseBody) -> dict:
    tasks = {task.task_id: task for task in course.tasks}
    return {
        "kind": kind,
        "purpose": item["purpose"],
        "content": item["content"],
        "stage": item["stage"],
        "audience": item["audience"],
        "questions": [tasks[task_id].prompt for task_id in item["task_ids"] if task_id in tasks],
    }


def image_request(item: dict, course: CourseBody) -> ModelRequest:
    return ModelRequest(
        "preparation",
        IMAGE_INSTRUCTIONS,
        material_context("image", item, course),
        ImageNote,
        deadline_key="preparation_image",
        media="image",
    )


def animation_request(item: dict, course: CourseBody) -> ModelRequest:
    return ModelRequest(
        "preparation",
        ANIMATION_INSTRUCTIONS,
        material_context("animation", item, course),
        AnimationDraft,
        deadline_key="preparation_animation",
    )
