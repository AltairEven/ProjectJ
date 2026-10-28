"""Requests for the pictures and animations a confirmed course lists (ADR-0017), and for the
preparation model to look at the pictures it must check (ADR-0026).

Only the resource's own description, the lesson's picture style, the paragraph or questions
it accompanies go to the model: no learner identity. A picture check also carries the story,
the question and its answer, for preparation only; nothing of it reaches the class.
"""

from typing import Literal

from project_j.contracts import Contract
from project_j.platform.models.contracts import ImageNote, ModelRequest
from project_j.preparation.contracts import CourseBody
from project_j.preparation.pictures import task_picture
from project_j.preparation.reading import passage
from project_j.preparation.task_types import filled

LOOK = "按style写的本课风格画，画面清楚，"
IMAGE_INSTRUCTIONS = (
    "为学生的课堂画一张教学插图。按purpose与content画，" + LOOK + "除非content要求不加文字；"
    "不画出questions的答案，不画真实人物或品牌；画完后在description里用一句简单英文说明画了什么。"
)
STORY_IMAGE_INSTRUCTIONS = (
    "为学生读的故事画一张插图，放在paragraph这一段旁边。按purpose与content，照paragraph这一段故事画，"
    + LOOK
    + "不加文字，不画真实人物或品牌；画完后在description里用一句简单英文说明画了什么。"
)
TASK_IMAGE_INSTRUCTIONS = (
    "为学生的一道看图题画一张图，这张图是题目的一部分。按purpose与content画，" + LOOK + "可以画出"
    "questions要考的东西，但不写出答案文字，不画出后续题的答案，不画真实人物或品牌；"
    "画完后在description里用一句简单英文说明画了什么。"
)
SVG_RULES = (
    "只用SVG绘图元素与animate、animateTransform、animateMotion、set等SMIL动画元素，"
    "根元素为svg并带viewBox，循环播放；不用script、style元素、foreignObject、image、"
    "外部链接或事件属性；"
)
ANIMATION_INSTRUCTIONS = (
    "为学生的课堂写一段会动的SVG动画。按purpose与content画，"
    + LOOK
    + SVG_RULES
    + "不演示questions的答案。description用一句简单英文说明动画内容。"
)
STORY_ANIMATION_INSTRUCTIONS = (
    "为学生读的故事写一段会动的SVG动画，放在paragraph这一段旁边。按purpose与content，"
    "照paragraph这一段故事演示，"
    + LOOK
    + SVG_RULES
    + "不加文字。description用一句简单英文说明动画内容。"
)
CHECK_INSTRUCTIONS = (
    "你在备课时检查学生课堂要用的图片，images按顺序是pictures里的第1、2…张。按form核对："
    "story是课文插图，看它画的是不是paragraph这一段里的场景、有没有画错或会误导理解的地方、"
    "风格是否符合style；options是一道选择题的选项图，看每张画的是不是它的option、几张图能不能分清、"
    "按story和question是不是只有correct为true的那张对得上、风格是否一致且符合style；"
    "question是看图题的题图，看图里画的是不是question要看、并且与answer相符的东西，"
    "图里有没有能直接对出答案的文字。pictures里每张图回一条：picture是编号，合格时ok为true、"
    "reason为null；不合格时ok为false，reason取wrong（画的不是它该画的，或与故事不符）、"
    "misleading（会误导理解）、answer_text（图里写着能对出答案的文字）、missing（没画出题目要看的东西）、"
    "alike（与别的选项图分不清）、also_fits（不止正确选项对得上）、style（风格不符）。"
    "分不清或不止一张对得上时，标在出问题的那几张上。只依据给出的内容和图片，不补造。"
)
CheckReason = Literal[
    "wrong", "misleading", "answer_text", "missing", "alike", "also_fits", "style"
]


class AnimationDraft(Contract):
    svg: str
    description: str


class PictureVerdict(Contract):
    picture: int
    ok: bool
    reason: CheckReason | None


class PictureCheck(Contract):
    pictures: tuple[PictureVerdict, ...]


def paragraph_of(story: str, number: int) -> str:
    return passage(story)[1][number - 1]


def material_context(kind: str, item: dict, course: CourseBody, story: str) -> dict:
    tasks = {task.task_id: task for task in course.tasks}
    context = {
        "kind": kind,
        "purpose": item["purpose"],
        "content": item["content"],
        "stage": item["stage"],
        "audience": item["audience"],
        "style": course.picture_style,
        "questions": [tasks[task_id].prompt for task_id in item["task_ids"] if task_id in tasks],
    }
    if item["stage"] == "story":
        context["paragraph"] = paragraph_of(story, item["paragraph"])
    return context


def image_request(item: dict, course: CourseBody, story: str) -> ModelRequest:
    instructions = (
        STORY_IMAGE_INSTRUCTIONS
        if item["stage"] == "story"
        else TASK_IMAGE_INSTRUCTIONS
        if task_picture(item)
        else IMAGE_INSTRUCTIONS
    )
    return ModelRequest(
        "preparation",
        instructions,
        material_context("image", item, course, story),
        ImageNote,
        deadline_key="preparation_image",
        media="image",
    )


def animation_request(item: dict, course: CourseBody, story: str) -> ModelRequest:
    story_part = item["stage"] == "story"
    return ModelRequest(
        "preparation",
        STORY_ANIMATION_INSTRUCTIONS if story_part else ANIMATION_INSTRUCTIONS,
        material_context("animation", item, course, story),
        AnimationDraft,
        deadline_key="preparation_animation",
    )


def check_request(
    items: list[dict], images: tuple[bytes, ...], course: CourseBody, story: str
) -> ModelRequest:
    """One look at a group's pictures: a story picture, or every picture of one question."""
    first = items[0]
    pictures = [{"picture": i, "content": item["content"]} for i, item in enumerate(items, 1)]
    context: dict = {"kind": "check", "style": course.picture_style}
    if first["stage"] == "story":
        context |= {
            "form": "story",
            "paragraph": paragraph_of(story, first["paragraph"]),
            "pictures": pictures,
        }
    else:
        task = next(task for task in course.tasks if task.task_id == first["task_ids"][0])
        context |= {"story": story, "question": task.prompt}
        if first["stage"] == "option":
            context |= {
                "form": "options",
                "pictures": [
                    picture
                    | {
                        "option": task.choice.options[item["option"]],
                        "correct": item["option"] == task.choice.correct,
                    }
                    for picture, item in zip(pictures, items, strict=True)
                ],
            }
        else:
            answer = task.choice.options[task.choice.correct] if task.choice else filled(task.fill)
            context |= {"form": "question", "answer": answer, "pictures": pictures}
    return ModelRequest(
        "preparation",
        CHECK_INSTRUCTIONS,
        context,
        PictureCheck,
        deadline_key="preparation_image_check",
        images=images,
    )


def verdicts(check: PictureCheck, count: int) -> dict[int, CheckReason | None] | None:
    """Picture number -> why it failed (None: it passed); None when the reply does not judge
    each picture exactly once, or fails one without saying why."""
    numbers = sorted(verdict.picture for verdict in check.pictures)
    if numbers != list(range(1, count + 1)):
        return None
    if any(not verdict.ok and verdict.reason is None for verdict in check.pictures):
        return None
    return {v.picture: None if v.ok else v.reason for v in check.pictures}
