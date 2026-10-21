import pytest
from pydantic import ValidationError

from project_j.contracts import RevisionRef
from project_j.preparation.contracts import CourseBody, Support, TaskDefinition
from project_j.preparation.packages import FIRST, course_tasks
from project_j.preparation.task_types import (
    ChoiceContent,
    FillContent,
    MatchContent,
    OpenContent,
    OrderContent,
    calibration,
    course_budget,
    task_minutes,
    timing_samples,
)

READING = "english.reading.facts"
MATERIAL = RevisionRef(object_id="material", revision=1)


def definition(purpose="answer", **fields):
    base = dict(
        task_id=fields.pop("task_id", f"{purpose}-task"),
        dimension_id=READING,
        purpose=purpose,
        prompt="What happens?",
        material_ref=MATERIAL,
        evidence="The paper bends.",
        acceptable_simulated=(),
        supports=(
            Support(text="Look again.", help={READING: "light_prompt"}),
            Support(text="Read the second line.", help={READING: "scaffold"}),
        ),
        exit_text="Next one.",
    )
    return TaskDefinition(**(base | fields))


def choice_task(variant="single", **fields):
    options = {"true_false": ("对", "错"), "true_false_not_given": ("对", "错", "文中没提")}
    item = ChoiceContent(variant=variant, options=options.get(variant, ("a", "b", "c")), correct=1)
    return definition("choice", choice=item, correct_text="对了。", **fields)


def order_task(items=4, **fields):
    item = OrderContent(variant="events", items=tuple(f"step {i}" for i in range(items)))
    return definition("order", order=item, correct_text="对了。", **fields)


def match_task(pairs=3, **fields):
    item = MatchContent(
        left=tuple(f"l{i}" for i in range(pairs)),
        right=tuple(f"r{i}" for i in range(pairs)),
        distractors=(),
    )
    return definition("match", match=item, correct_text="对了。", **fields)


def fill_task(blanks=1, **fields):
    item = FillContent(
        variant="free",
        text=" ".join(["She ___ it."] * blanks),
        answers=(("puts",),) * blanks,
        distractors=(),
    )
    return definition("fill", fill=item, correct_text="对了。", **fields)


def open_task(**fields):
    item = OpenContent(variant="retell", points=("first", "then"), guidance="Say both.")
    return definition("open", open=item, **fields)


def test_program_tasks_carry_exactly_their_own_content():
    task = choice_task()
    assert task.type_version == 1 and task.choice.correct == 1
    with pytest.raises(ValidationError):  # A choice task without its content.
        definition(purpose="choice", correct_text="对了。")
    with pytest.raises(ValidationError):  # Content of another type.
        definition(
            purpose="choice",
            order=OrderContent(variant="events", items=("a", "b", "c")),
            correct_text="对了。",
        )
    with pytest.raises(ValidationError):  # Only program-judged tasks say a prepared correct line.
        definition(purpose="answer", correct_text="对了。")
    with pytest.raises(ValidationError):  # A clicked task says one when answered right.
        definition(purpose="choice", choice=task.choice)
    with pytest.raises(ValidationError):  # Only an offscreen task has its own minutes.
        definition(purpose="answer", minutes=5)
    assert open_task().correct_text is None


def old_course_json() -> dict:
    """A course saved before ADR-0020: a model budget summing to 30, no new task fields."""
    ref = {"object_id": "x", "revision": 1}
    task = definition().model_dump(
        mode="json",
        exclude={"choice", "order", "match", "fill", "open", "correct_text", "minutes"}
        | {"type_version"},
    )
    goal = {"dimension_id": READING, "description": "目标"}
    return {
        "status": "checked",
        **{name: ref for name in ("demand_ref", "state_ref", "plan_ref", "jane_ref", "brief_ref")},
        "material_refs": [MATERIAL.model_dump()],
        "primary_goal": goal,
        "auxiliary_goal": goal | {"dimension_id": "english.speaking.report"},
        "budget": {"opening": 3, "reading": 7, "expression": 17, "closing": 3},
        "lesson_type": "observation",
        "tasks": [task],
        "demand_mapping": [{"field": "f", "requirement": "r", "implementation": "i"}],
        "parent_preview": {},
        "limitations": [],
    }


def test_an_old_saved_course_still_loads():
    course = CourseBody.model_validate(old_course_json())
    assert all(task.choice is None and task.type_version == 1 for task in course.tasks)


def course_with_budget(budget: dict) -> CourseBody:
    return CourseBody.model_validate(old_course_json() | {"budget": budget})


def test_a_course_budget_may_reach_but_not_pass_forty_minutes():
    assert course_with_budget({"opening": 2, "questions": 36, "closing": 2})
    with pytest.raises(ValidationError):
        course_with_budget({"opening": 2, "questions": 37, "closing": 2})
    with pytest.raises(ValidationError):
        course_with_budget({"opening": 0, "questions": 30})


def test_minutes_follow_the_registered_estimates():
    assert task_minutes(choice_task("true_false")) == 0.5
    assert task_minutes(choice_task("single")) == 1
    assert task_minutes(choice_task("true_false_not_given")) == 1
    assert task_minutes(order_task(items=4)) == 1.5
    assert task_minutes(match_task(pairs=3)) == 1.25
    assert task_minutes(fill_task(blanks=2)) == 1.5
    assert task_minutes(definition()) == 2 and task_minutes(open_task()) == 4
    assert task_minutes(definition("point", dimension_id="science.observation.report")) == 2
    offscreen = definition("offscreen", dimension_id="science.observation.report", minutes=7)
    assert task_minutes(offscreen) == 7


def test_course_budget_rounds_each_part_up():
    budget = course_budget((definition(), choice_task("true_false")), story="word " * 90)
    assert budget == {"opening": 2, "reading": 3, "questions": 3, "closing": 2}


def test_the_template_course_estimates_within_the_bounds():
    from test_preparation_drafting import STORY

    tasks = course_tasks(FIRST, STORY, MATERIAL, READING, "english.speaking.report")
    budget = course_budget(tasks, STORY)
    assert set(budget) == {"opening", "reading", "questions", "read_aloud", "offscreen", "closing"}
    assert budget["offscreen"] == 7 and 20 <= sum(budget.values()) <= 40


def fact_with(*attempts):
    return {"attempts": list(attempts)}


def attempt(task_id, instance, started, judged, judgment="correct"):
    return {
        "task_id": task_id,
        "task_instance_id": instance,
        "task_started_ms": started,
        "judged_ms": judged,
        "judgment": judgment,
    }


class Course:
    def __init__(self, *tasks):
        self.tasks = tasks


def test_timing_samples_run_from_question_to_last_judgment():
    course = Course(definition(task_id="q"), choice_task(task_id="c"))
    fact = fact_with(
        attempt("q", "i1", 1000, 61000, "incorrect"),
        attempt("q", "i1", 1000, 181000),  # Three minutes from the question to the last judgment.
        attempt("c", "i2", 200000, 230000),
    )
    assert timing_samples(fact, course) == [("answer", 3.0, 2), ("choice", 0.5, 1)]


def test_facts_without_times_or_offscreen_tasks_give_no_samples():
    offscreen = definition(
        "offscreen", task_id="o", dimension_id="science.observation.report", minutes=7
    )
    course = Course(definition(task_id="q"), offscreen)
    old = {"task_id": "q", "task_instance_id": "i1", "judgment": "correct"}  # Before QT-1.
    fact = fact_with(old, attempt("o", "i2", 0, 600000, "reported"))
    assert timing_samples(fact, course) == []


def test_calibration_needs_five_samples_and_bounds_each_ratio():
    four = [("answer", 4.0, 2)] * 4
    assert calibration(four) == {}  # Too few: the estimate stays as registered.
    assert calibration([*four, ("answer", 4.0, 2)]) == {"answer": 2.0}
    # One long pause (60 minutes on a 2-minute estimate) counts as 3, not 30.
    assert calibration([("answer", 60.0, 2)] + [("answer", 2.0, 2)] * 4) == {"answer": 1.4}
    assert calibration([("answer", 0.1, 2)] * 5) == {"answer": 0.3}


def test_calibration_takes_the_latest_twenty_samples():
    samples = [("choice", 3.0, 1)] * 20 + [("choice", 1.0, 1)] * 20
    assert calibration(samples) == {"choice": 1.0}


def test_factors_scale_the_question_estimate_but_never_offscreen():
    tasks = (
        definition(),
        definition("offscreen", dimension_id="science.observation.report", minutes=7),
    )
    plain = course_budget(tasks, story="word " * 60)
    scaled = course_budget(tasks, story="word " * 60, factors={"answer": 2.0, "offscreen": 3.0})
    assert (plain["questions"], scaled["questions"]) == (2, 4)
    assert plain["offscreen"] == scaled["offscreen"] == 7
