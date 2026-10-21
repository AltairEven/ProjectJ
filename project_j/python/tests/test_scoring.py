import asyncio
from uuid import uuid4

import pytest
from support import complete_story, correct_response, deliver_all
from test_program_tasks import to_clicked, wrong_choice

from project_j.evaluation.scoring import (
    Question,
    lesson_scores,
    pronunciation_score,
    questions,
    recent_scores,
    rule_score,
)


def question(wrong=0, correct=True, hints=0, exited=False, skipped=False, purpose="choice"):
    attempts = [{"judgment": "incorrect"}] * wrong + ([{"judgment": "correct"}] if correct else [])
    return Question("t", "i", purpose, "english.reading.facts", attempts, hints, exited, skipped)


def reading(*tries):
    attempts = [
        {
            "judgment": judgment,
            "pronunciation": {"status": "scored", "accuracy": accuracy, "completeness": complete}
            if accuracy is not None
            else {"status": "unknown"},
        }
        for accuracy, complete, judgment in tries
    ]
    return Question(
        "r", "i", "read_aloud", "english.speaking.pronunciation", attempts, 0, False, False
    )


@pytest.mark.parametrize(
    ("wrong", "hints", "score"),
    [(0, 0, 10), (1, 0, 7), (2, 0, 4), (3, 0, 0), (0, 2, 8), (1, 1, 6), (2, 5, 0)],
)
def test_rule_score_follows_wrong_answers_then_hints(wrong, hints, score):
    assert rule_score(question(wrong=wrong, correct=True, hints=hints))[0] == score


def test_rule_score_reasons_read_for_the_student():
    assert rule_score(question()) == (10, "一次答对")
    assert rule_score(question(wrong=1, hints=1)) == (6, "错1次后答对，用了1次提示")


def test_never_correct_after_trying_is_zero_but_never_answering_is_unknown():
    assert rule_score(question(wrong=2, correct=False, exited=True))[0] == 0
    assert rule_score(question(wrong=1, correct=False, skipped=True))[0] == 0
    assert rule_score(question(wrong=0, correct=False, hints=1, skipped=True)) == (None, "没有作答")
    assert (
        rule_score(question(wrong=1, correct=False))[0] is None
    )  # Still current at stop or close.


def test_reading_takes_the_last_scored_try_less_one_per_earlier_misread():
    assert pronunciation_score(reading((80, 90, "incorrect"), (95, 100, "correct")))[0] == 8.5
    assert pronunciation_score(reading((None, None, "unclear")))[0] is None
    assert pronunciation_score(reading((10, 50, "incorrect"), (20, 50, "correct")))[0] == 0.0
    assert pronunciation_score(reading((80, 90, "incorrect")))[0] is None  # Cut off.


def test_questions_group_attempts_hints_and_exits_by_task_instance(application):
    async def scenario():
        core = await to_clicked(application)
        task = core.current_task()
        instance = core.task_instance_id
        await application.adapter.response(str(uuid4()), wrong_choice(task))
        deliver_all(core)
        await application.adapter.response(str(uuid4()), None, "help")
        deliver_all(core)
        await application.adapter.response(str(uuid4()), correct_response(task))
        deliver_all(core)
        core.control(str(uuid4()), "stop")
        sealed = application.seal().body
        found = next(q for q in questions(sealed) if q.task_instance_id == instance)
        assert (len(found.attempts), found.hints, found.exited, found.skipped) == (
            2,
            1,
            False,
            False,
        )
        assert rule_score(found) == (6, "错1次后答对，用了1次提示")
        current = [q for q in questions(sealed) if q.purpose == "order"]
        assert current and rule_score(current[0]) == (None, "没有作答")

    asyncio.run(scenario())


def evaluation(activity, *entries):
    return {
        "activity_id": activity,
        "alignment": {"tasks_materials": {"object_id": "course", "revision": 1}},
        "scores": [
            {"purpose": purpose, "dimension_id": dimension, "score": score}
            for purpose, dimension, score in entries
        ],
    }


READING, PRONUNCIATION = "english.reading.facts", "english.speaking.pronunciation"


def test_a_lesson_averages_known_scores_only():
    lesson = lesson_scores(
        evaluation("a1", ("choice", READING, 7), ("choice", READING, 10), ("order", READING, None))
    )
    assert lesson["by_type"] == {
        "choice": {"average": 8.5, "count": 2},
        "order": {"average": None, "count": 0},
    }
    assert lesson["by_dimension"] == {READING: {"average": 8.5, "count": 2}}
    assert lesson["unknown"] == 1 and lesson["course_ref"] == {"object_id": "course", "revision": 1}


def test_a_lesson_of_unknowns_has_no_average():
    lesson = lesson_scores(evaluation("a1", ("answer", READING, None)))
    assert lesson["by_type"]["answer"] == {"average": None, "count": 0} and lesson["unknown"] == 1
    assert lesson_scores({**evaluation("a2"), "scores": []})["by_type"] == {}
    old = evaluation("a3")
    del old["scores"]  # Evaluated before scoring.
    assert lesson_scores(old)["unknown"] == 0


def test_recent_scores_pool_the_last_five_lessons_by_question():
    lessons = [evaluation("a0", ("answer", READING, 0))] + [
        evaluation(f"a{n}", ("answer", READING, 10), ("read_aloud", PRONUNCIATION, 8))
        for n in range(1, 5)
    ]
    lessons.append(evaluation("a5", ("answer", READING, 4), ("answer", READING, 10)))
    recent = recent_scores(lessons)
    assert recent["lessons"] == 5  # a0 is too old.
    assert recent["by_type"]["answer"] == {"average": 9.0, "count": 6}  # By question, not lesson.
    assert recent["by_dimension"][PRONUNCIATION] == {"average": 8.0, "count": 4}
    assert recent_scores([]) == {"lessons": 0, "by_type": {}, "by_dimension": {}}


def test_a_whole_class_scores_through_the_evaluation(application):
    _, fact = asyncio.run(complete_story(application))
    found = {q.task_id: q for q in questions(fact.body)}
    assert all(rule_score(found[t])[0] == 10 for t in ("choice-first-try", "fill-puts"))


def test_the_dashboard_lists_each_lessons_scores(application):
    from support import LEARNER

    assert application.dashboard(LEARNER)["scores"] == []  # A new student: nothing yet.
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "无额外补充", [])
    asyncio.run(application.evaluate("e1", LEARNER, activity, "after_class"))
    first = application.dashboard(LEARNER)["scores"]
    assert len(first) == 1 and first[0]["activity_id"] == activity
    assert first[0]["by_type"]["choice"] == {"average": 10.0, "count": 1}
    assert first[0]["occurred_at"] and first[0]["course_ref"] == fact.body["course_ref"]
    # A correction rewrites the evaluation: the lesson still shows once, from the new version.
    application.feedback(
        "fb2", LEARNER, activity, "选择题是我代答的。", [{"task_id": "choice-first-try"}]
    )
    asyncio.run(application.evaluate("e2", LEARNER, activity, "final_feedback"))
    after = application.dashboard(LEARNER)["scores"]
    assert len(after) == 1 and after[0]["by_type"]["choice"] == {"average": None, "count": 0}
    assert after[0]["unknown"] == first[0]["unknown"] + 1
