"""Story pictures and picture questions in a drafted lesson (ADR-0026): the rules only count,
place and attach them; how good a picture is is checked later by looking at it."""

import pytest
from pydantic import ValidationError
from support import LEARNER
from test_preparation_model import (
    NEW_STORY,
    READING,
    science_draft,
    science_lesson,
    support,
    typed,
)

from project_j.preparation.contracts import CourseBody, ResourceSpec
from project_j.preparation.drafting import CourseDraft
from project_j.preparation.pictures import picture_problems
from project_j.preparation.reading import passage

FIRST = NEW_STORY.split(" The kitchen", 1)[0]
STORY = FIRST + "\n\n" + NEW_STORY[len(FIRST) + 1 :]  # Two paragraphs, the same words.
STYLE = "Soft watercolor picture-book style with warm kitchen colors."
OPTIONS = ("water", "milk", "juice")


def choice(task_id, prompt, evidence, options, help_text):
    return typed(
        {
            "task_id": task_id,
            "dimension_id": READING,
            "purpose": "choice",
            "prompt": prompt,
            "evidence": evidence,
            "acceptable_answers": [],
            "known_incorrect": [],
            "supports": [
                support(help_text, (READING, "light_prompt")),
                support(f"Read this again: {evidence}", (READING, "scaffold")),
            ],
            "exit_text": "Let's leave this one for now.",
            "optional": False,
            "choice": {"variant": "single", "options": list(options), "correct": 0},
            "correct_text": "Yes, that is right.",
        }
    )


def picture(resource_id, stage, **fields):
    return {
        "resource_id": resource_id,
        "kind": "image",
        "stage": stage,
        "purpose": f"Picture {resource_id}",
        "content": f"What {resource_id} shows.",
        "task_ids": [],
        "audience": "student",
        "required": False,
        "fallback": "Read the words.",
        "paragraph": None,
        "option": None,
        "picture_question": False,
    } | fields


def picture_draft(context):
    """The science lesson on a two-paragraph story with two picture questions first: picture
    options on cup-choice, a question picture on cup-light, and a story picture for each
    paragraph (a still one and an animation)."""
    draft = science_draft(context)
    draft["story"] = STORY
    draft["tasks"] = [
        choice(
            "cup-choice",
            "Which one shows what Sam puts in the cup?",
            "He fills it with water.",
            OPTIONS,
            'Find the word "fills".',
        ),
        choice(
            "cup-light",
            "How is the cup after Sam pours the water out?",
            "The cup is light again.",
            ("light", "heavy", "wet"),
            'Find the word "pours".',
        ),
        *draft["tasks"],
    ]
    draft["resources"] = [
        picture("cup-scene", "story", paragraph=1),
        picture("kitchen-motion", "story", paragraph=2, kind="animation"),
        *(
            picture(f"cup-option-{i}", "option", option=i, task_ids=["cup-choice"])
            for i in range(len(OPTIONS))
        ),
        picture("cup-look", "question", picture_question=True, task_ids=["cup-light"]),
    ]
    draft["picture_style"] = STYLE
    return draft


def draft(change=None) -> CourseDraft:
    built = picture_draft({"requirements": []})
    if change:
        change(built)
    return CourseDraft.model_validate(built)


def problems(change=None, paragraphs=2) -> list[str]:
    return [text for code, text in picture_problems(draft(change), paragraphs) if code == "picture"]


def resource(built, resource_id):
    return next(item for item in built["resources"] if item["resource_id"] == resource_id)


def test_paragraphs_match_the_page_passage():
    assert passage("Title\n\nOne.\n\nTwo.") == ("Title", ["One.", "Two."])
    assert passage("Only one line.") == ("", ["Only one line."])
    assert passage("Line one\nline two\n\nNext.") == ("", ["Line one\nline two", "Next."])
    assert passage("T\n  \nA.") == ("T", ["A."])


def test_the_picture_lesson_passes():
    assert picture_problems(draft(), 2) == []


def test_a_lesson_without_pictures_passes():
    def plain(built):
        built["resources"], built["picture_style"] = [], None

    assert problems(plain) == []


def test_story_pictures_are_counted_and_placed():
    def three(built):
        built["resources"].append(picture("cup-end", "story", paragraph=1))

    assert any("at most 2 story pictures" in text for text in problems(three))
    assert any("more than one story picture" in text for text in problems(three))

    def outside(built):
        resource(built, "cup-scene")["paragraph"] = 3

    assert any("cup-scene: the paragraph must be 1 to 2" in text for text in problems(outside))


def test_story_pictures_are_optional_student_pictures():
    for field, value in (("task_ids", ["cup-fact"]), ("audience", "parent"), ("required", True)):

        def wrong(built, field=field, value=value):
            resource(built, "cup-scene")[field] = value

        assert any(text.startswith("cup-scene:") for text in problems(wrong)), field


def test_picture_questions_are_limited_to_two():
    def three(built):
        built["tasks"].insert(
            2,
            choice(
                "cup-heavy",
                "How is the cup with water in it?",
                "The cup is heavy now.",
                ("heavy", "light", "empty"),
                'Find the word "now".',
            ),
        )
        built["resources"].append(
            picture("heavy-look", "question", picture_question=True, task_ids=["cup-heavy"])
        )

    assert any("at most 2 questions" in text for text in problems(three))


def test_option_pictures_cover_each_option_once():
    def missing(built):
        built["resources"].remove(resource(built, "cup-option-2"))

    def repeated(built):
        resource(built, "cup-option-2")["option"] = 1

    def true_false(built):
        built["tasks"][0]["choice"] = {"variant": "true_false", "options": ["True", "False"]}
        built["tasks"][0]["choice"]["correct"] = 0

    def five(built):
        built["tasks"][0]["choice"]["options"] = ["water", "milk", "juice", "tea", "soup"]
        built["resources"] += [
            picture(f"cup-option-{i}", "option", option=i, task_ids=["cup-choice"]) for i in (3, 4)
        ]

    for change in (missing, repeated, true_false, five):
        assert any(text.startswith("cup-choice:") for text in problems(change)), change.__name__


def test_pictures_attach_to_the_right_task_types():
    def option_on_answer(built):
        resource(built, "cup-option-0")["task_ids"] = ["cup-fact"]

    def look_on_point(built):
        resource(built, "cup-look")["task_ids"] = ["point-cup"]

    assert any("cup-option-0" in text for text in problems(option_on_answer))
    assert any("cup-look" in text for text in problems(look_on_point))


def test_task_pictures_are_optional_still_images_of_one_question():
    for name in ("cup-option-0", "cup-look"):
        for field, value in (
            ("required", True),
            ("kind", "animation"),
            ("audience", "parent"),
            ("task_ids", ["cup-choice", "cup-light"]),
            ("task_ids", []),
        ):

            def wrong(built, name=name, field=field, value=value):
                resource(built, name)[field] = value

            assert any(name in text for text in problems(wrong)), (name, field, value)


def test_one_picture_form_per_question():
    def both(built):
        built["resources"].append(picture("choice-look", "question", task_ids=["cup-choice"]))

    assert any("picture options or a question picture" in text for text in problems(both))

    def helped(built):
        built["resources"].append(picture("choice-help", "help", task_ids=["cup-choice"]))

    assert problems(helped) == []


def test_pictures_need_a_style():
    def unstyled(built):
        built["picture_style"] = None

    assert any("picture_style" in text for text in problems(unstyled))

    def only_a_help_picture(built):
        built["resources"] = [picture("choice-help", "help", task_ids=["cup-choice"])]
        built["picture_style"] = None

    assert any("picture_style" in text for text in problems(only_a_help_picture))


def test_picture_fields_belong_to_their_stage():
    def stray(built):
        resource(built, "cup-look")["paragraph"] = 1

    assert any("cup-look" in text for text in problems(stray))


def test_an_option_picture_without_its_option_is_repaired_not_a_crash():
    def unnumbered(built):
        resource(built, "cup-option-1")["option"] = None

    assert any("cup-option-1: an option picture names its option" in text
               for text in problems(unnumbered))  # fmt: skip


def test_checked_course_keeps_pictures_and_style(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, picture_draft)
    course = CourseBody.model_validate(application.store.read(course_ref, LEARNER).body)
    assert course.picture_style == STYLE
    pictures = {item.resource_id: item for item in course.resources}
    assert (pictures["cup-scene"].stage, pictures["cup-scene"].paragraph) == ("story", 1)
    assert [pictures[f"cup-option-{i}"].option for i in range(3)] == [0, 1, 2]
    assert pictures["cup-look"].picture_question


def test_old_course_without_picture_fields_loads(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, science_draft)
    body = application.store.read(course_ref, LEARNER).body
    body = {key: value for key, value in body.items() if key != "picture_style"}
    body["resources"] = [
        {
            key: value
            for key, value in item.items()
            if key not in {"paragraph", "option", "picture_question"}
        }
        for item in body["resources"]
    ]
    course = CourseBody.model_validate(body)
    assert course.picture_style is None and course.resources


def test_resource_spec_guards_picture_fields():
    base = picture("x", "story", paragraph=1)
    ResourceSpec.model_validate(base)
    with pytest.raises(ValidationError):
        ResourceSpec.model_validate(base | {"paragraph": None})
    with pytest.raises(ValidationError):
        ResourceSpec.model_validate(base | {"stage": "question", "paragraph": None, "option": 1})
    with pytest.raises(ValidationError):
        ResourceSpec.model_validate(
            base | {"stage": "help", "paragraph": None, "picture_question": True}
        )
