"""What the picture, animation and picture-check requests carry (ADR-0026)."""

from test_picture_drafting import OPTIONS, STORY, STYLE, picture_draft

from project_j.platform.models.contracts import identity_keys, solid_png
from project_j.preparation.contracts import CourseBody
from project_j.preparation.drafting import CourseDraft
from project_j.preparation.materials import (
    PictureCheck,
    PictureVerdict,
    animation_request,
    check_request,
    image_request,
    verdicts,
)

SEEN = f"Sam's Cup\n\n{STORY}"
ANSWER_RULE = "不画出questions的答案"


def course() -> CourseBody:
    """The picture lesson's tasks and resources as a course body (no rules needed here)."""
    draft = CourseDraft.model_validate(picture_draft({"requirements": []}))
    material = {"object_id": "m", "revision": 1}
    tasks = [
        {
            "task_id": task.task_id,
            "dimension_id": task.dimension_id,
            "purpose": task.purpose,
            "prompt": task.prompt,
            "material_ref": material,
            "evidence": task.evidence,
            "acceptable_simulated": [],
            "supports": [
                {"text": s.text, "help": {e.dimension_id: e.level for e in s.help}}
                for s in task.supports
            ],
            "exit_text": task.exit_text,
            "optional": task.optional,
            "minutes": task.minutes,
            "correct_text": task.correct_text,
        }
        | ({"choice": task.choice} if task.choice else {})
        for task in draft.tasks
    ]
    ref_ = {"object_id": "x", "revision": 1}
    goal = {"dimension_id": "english.reading.facts", "description": "Read facts."}
    return CourseBody.model_validate(
        {
            "demand_ref": ref_,
            "state_ref": ref_,
            "plan_ref": ref_,
            "jane_ref": ref_,
            "brief_ref": ref_,
            "material_refs": [material],
            "primary_goal": goal,
            "auxiliary_goal": goal,
            "budget": {"read": 5},
            "lesson_type": "observation",
            "tasks": tasks,
            "demand_mapping": [{"field": "f", "requirement": "r", "implementation": "i"}],
            "parent_preview": {},
            "limitations": [],
            "picture_style": STYLE,
            "resources": [item.model_dump() for item in draft.resources],
        }
    )


def item(body: CourseBody, resource_id: str) -> dict:
    return next(r.model_dump() for r in body.resources if r.resource_id == resource_id)


def group(body: CourseBody, *ids: str) -> list[dict]:
    return [item(body, resource_id) for resource_id in ids]


OPTION_IDS = tuple(f"cup-option-{i}" for i in range(len(OPTIONS)))


def test_every_picture_request_carries_the_style():
    body = course()
    plain = item(body, "cup-look") | {"picture_question": False}
    helping = plain | {"stage": "help", "kind": "animation"}
    requests = [
        image_request(plain, body, SEEN),
        animation_request(helping, body, SEEN),
        image_request(item(body, "cup-scene"), body, SEEN),
        animation_request(item(body, "kitchen-motion"), body, SEEN),
        image_request(item(body, "cup-option-1"), body, SEEN),
        check_request(group(body, *OPTION_IDS), (solid_png(),) * 3, body, SEEN),
    ]
    assert all(request.context["style"] == STYLE for request in requests)
    assert all("按style写的本课风格画" in request.instructions for request in requests[:5])


def test_story_picture_draws_its_paragraph_without_questions():
    body = course()
    for request in (
        image_request(item(body, "kitchen-motion") | {"kind": "image"}, body, SEEN),
        animation_request(item(body, "kitchen-motion"), body, SEEN),
    ):
        assert request.context["paragraph"] == STORY.split("\n\n")[1]
        assert request.context["questions"] == []
        assert ANSWER_RULE not in request.instructions and "不演示questions" not in (
            request.instructions
        )


def test_task_picture_may_show_what_is_asked():
    body = course()
    for resource_id in ("cup-option-0", "cup-look"):
        request = image_request(item(body, resource_id), body, SEEN)
        assert request.context["questions"] and ANSWER_RULE not in request.instructions
        assert "不写出答案文字" in request.instructions


def test_question_and_help_pictures_keep_the_answer_rule():
    body = course()
    plain = item(body, "cup-look") | {"picture_question": False}
    assert ANSWER_RULE in image_request(plain, body, SEEN).instructions
    assert ANSWER_RULE in image_request(plain | {"stage": "help"}, body, SEEN).instructions


def test_check_request_carries_question_answer_and_pictures():
    body = course()
    options = check_request(group(body, *OPTION_IDS), (solid_png(),) * 3, body, SEEN)
    assert options.deadline_key == "preparation_image_check" and len(options.images) == 3
    assert options.purpose == "preparation" and options.media is None
    context = options.context
    assert context["kind"] == "check" and context["form"] == "options"
    assert context["question"] == "Which one shows what Sam puts in the cup?"
    assert [p["option"] for p in context["pictures"]] == list(OPTIONS)
    assert [p["correct"] for p in context["pictures"]] == [True, False, False]
    assert [p["picture"] for p in context["pictures"]] == [1, 2, 3]
    assert context["story"] == SEEN and not identity_keys(context)

    look = check_request(group(body, "cup-look"), (solid_png(),), body, SEEN).context
    assert look["form"] == "question" and look["answer"] == "light"

    story = check_request(group(body, "cup-scene"), (solid_png(),), body, SEEN).context
    assert story["form"] == "story" and story["paragraph"] == STORY.split("\n\n")[0]
    assert "question" not in story and "story" not in story


def ok(number):
    return PictureVerdict(picture=number, ok=True, reason=None)


def bad(number, reason):
    return PictureVerdict(picture=number, ok=False, reason=reason)


def test_check_reply_must_cover_each_picture_once():
    assert verdicts(PictureCheck(pictures=(ok(1), ok(2))), 3) is None
    assert verdicts(PictureCheck(pictures=(ok(1), ok(1), ok(2))), 3) is None
    assert verdicts(PictureCheck(pictures=(ok(1), bad(2, None))), 2) is None
    assert verdicts(PictureCheck(pictures=(ok(1), bad(2, "alike"))), 2) == {1: None, 2: "alike"}
    assert verdicts(PictureCheck(pictures=(ok(2), ok(1))), 2) == {1: None, 2: None}
