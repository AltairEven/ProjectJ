import asyncio

import pytest
from pydantic import ValidationError
from support import start
from test_preparation_drafting import STORY, check, initial

from project_j.preparation.contracts import ResourceSpec
from project_j.preparation.packages import FIRST, template_draft
from project_j.preparation.voice import goal_text, question_speech, question_text


@pytest.fixture
def course(application):
    demand = initial(application)[0]
    return check(demand, template_draft(demand, FIRST, STORY)).course


def test_audio_resources_follow_the_course_text(course):
    resources = {item.resource_id: item for item in course.resources}
    assert resources["voice:goal"].text == goal_text(course)
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
        f"这一段约{offscreen.minutes}分钟；做完点“返回”，再说实际结果。"
    )
    # A course saved before ADR-0020 keeps its minutes in the budget.
    old = offscreen.model_copy(update={"minutes": None})
    assert "这一段约9分钟" in question_text(old, {"offscreen": 9})


def test_clicked_tasks_prepare_every_line_jane_may_say(course):
    from project_j.preparation.voice import WRONG_TEXT

    resources = {item.resource_id: item for item in course.resources}
    choice = next(task for task in course.tasks if task.purpose == "choice")
    expected = {
        "correct": choice.correct_text,
        "wrong-1": WRONG_TEXT + choice.supports[0].text,
        "wrong-2": WRONG_TEXT + choice.supports[1].text,
        "hint-1": choice.supports[0].text,
        "hint-2": choice.supports[1].text,
        "exit": choice.exit_text,
    }
    for name, text in expected.items():
        item = resources[f"voice:{choice.task_id}:{name}"]
        assert (item.text, item.kind, item.required) == (text, "audio", False)
    answer = next(task for task in course.tasks if task.purpose == "answer")
    assert not any(key.startswith(f"voice:{answer.task_id}:") for key in resources)


def test_goal_mentions_observation_only_with_an_offscreen_task(course):
    assert "离屏短观察" in goal_text(course)
    plain = course.model_copy(
        update={"tasks": tuple(task for task in course.tasks if task.purpose != "offscreen")}
    )
    assert "纸桥" not in goal_text(plain) and "离屏" not in goal_text(plain)
    review = course.model_copy(update={"lesson_type": "review"})
    assert goal_text(review).startswith("我是Jane。先复习读过的故事")


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
    assert core.actions[core.pending].text == goal_text(core.course)


def test_a_statement_over_the_synthesis_limit_is_left_to_live_speech(application):
    demand = initial(application)[0]
    draft = template_draft(demand, FIRST, STORY)
    draft["tasks"][0]["prompt"] = "请看故事。" * 250  # 1250 characters.
    course = check(demand, draft).course
    ids = {item.resource_id for item in course.resources}
    assert f"voice:{draft['tasks'][0]['task_id']}" not in ids and "voice:goal" in ids
