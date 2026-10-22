import asyncio

import pytest
from pydantic import ValidationError
from support import start
from test_preparation_drafting import STORY, check, initial

from project_j.preparation.contracts import ResourceSpec
from project_j.preparation.packages import FIRST, template_draft
from project_j.preparation.voice import opening_text, question_speech, question_text


@pytest.fixture
def course(application):
    demand = initial(application)[0]
    return check(demand, template_draft(demand, FIRST, STORY)).course


def test_audio_resources_follow_the_course_text(course):
    resources = {item.resource_id: item for item in course.resources}
    assert resources["voice:goal"].text == opening_text(course)
    assert resources["voice:goal"].task_ids == ()
    for task in course.tasks:
        statement = resources[f"voice:{task.task_id}"]
        assert statement.text == question_speech(task, course.budget)
        assert (statement.kind, statement.required, statement.task_ids) == (
            "audio",
            False,
            (task.task_id,),
        )
        if task.purpose == "read_aloud":
            demo = resources[f"demo:{task.task_id}"]
            assert (demo.text, demo.required, demo.stage) == (task.reference_text, True, "help")
            # The passage is not read to the child before the child reads it.
            assert statement.text == task.prompt
            assert task.reference_text in question_text(task, course.budget)
        else:
            assert f"demo:{task.task_id}" not in resources
    offscreen = next(task for task in course.tasks if task.purpose == "offscreen")
    assert question_text(offscreen, course.budget).endswith(
        f"This takes about {offscreen.minutes} minutes. When you are done, "
        'press "Back" and tell me what really happened.'
    )
    # A course saved before ADR-0020 keeps its minutes in the budget.
    old = offscreen.model_copy(update={"minutes": None})
    assert "This takes about 9 minutes" in question_text(old, {"offscreen": 9})


def test_clicked_tasks_prepare_every_line_jane_may_say(course):
    from project_j.preparation.voice import WRONG_TEXT

    resources = {item.resource_id: item for item in course.resources}
    choice = next(task for task in course.tasks if task.purpose == "choice")
    expected = {
        "correct": choice.correct_text,
        "wrong-1": f"{WRONG_TEXT} {choice.supports[0].text}",
        "wrong-2": f"{WRONG_TEXT} {choice.supports[1].text}",
        "hint-1": choice.supports[0].text,
        "hint-2": choice.supports[1].text,
        "exit": choice.exit_text,
    }
    for name, text in expected.items():
        item = resources[f"voice:{choice.task_id}:{name}"]
        assert (item.text, item.kind, item.required) == (text, "audio", False)
    answer = next(task for task in course.tasks if task.purpose == "answer")
    assert not any(key.startswith(f"voice:{answer.task_id}:") for key in resources)


def test_opening_is_the_reading_tip_and_how_to_start(course):
    assert opening_text(course) == (
        f"I'm Jane. {course.reading_focus} Press Start reading when you are ready."
    )
    review = course.model_copy(update={"lesson_type": "review"})
    assert opening_text(review) == (
        f"I'm Jane. You have read this story before. {course.reading_focus} "
        "Press Start reading when you are ready."
    )
    old = course.model_copy(update={"reading_focus": None})
    assert opening_text(old) == (
        "I'm Jane. Read the story on your own. Press Start reading when you are ready."
    )


def resource(**changes):
    fields = {
        "resource_id": "r",
        "kind": "audio",
        "purpose": "p",
        "content": "c",
        "task_ids": (),
        "audience": "student",
        "required": False,
        "fallback": "f",
        "text": "Listen.",
    }
    return ResourceSpec(**(fields | changes))


def test_audio_resource_needs_its_text():
    assert resource().text == "Listen."
    for bad in ({"text": None}, {"text": "x" * 1001}, {"kind": "image"}):
        with pytest.raises(ValidationError):
            resource(**bad)
    assert resource(kind="image", text=None).stage == "question"


def test_harness_says_the_shared_texts(application):
    core = asyncio.run(start(application))
    assert core.actions[core.pending].text == opening_text(core.course)


def test_a_statement_over_the_synthesis_limit_is_left_to_live_speech(application):
    demand = initial(application)[0]
    draft = template_draft(demand, FIRST, STORY)
    draft["tasks"][0]["prompt"] = "Read the story. " * 80  # 1280 characters.
    course = check(demand, draft).course
    ids = {item.resource_id for item in course.resources}
    assert f"voice:{draft['tasks'][0]['task_id']}" not in ids and "voice:goal" in ids
