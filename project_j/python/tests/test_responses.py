from typing import get_args

import pytest
from test_task_types import choice_task, definition, fill_task, match_task, order_task

from project_j.classroom.responses import JUDGES, describe, judge, presentation, shuffled
from project_j.evaluation.scoring import SCORING
from project_j.preparation.contracts import TaskDefinition
from project_j.preparation.task_types import (
    LADDERS,
    PROGRAM_JUDGED,
    TYPE_INSTRUCTIONS,
    FillContent,
    MatchContent,
    OrderContent,
)


def positions_of(shown: list[str], wanted) -> list[int]:
    return [shown.index(item) for item in wanted]


def test_display_hides_the_answer_and_judging_maps_positions_back():
    task = order_task().model_copy(
        update={
            "order": OrderContent(
                variant="events", items=("A first.", "B second.", "C third.", "D fourth.")
            )
        }
    )
    shown = presentation(task)
    assert sorted(shown["items"]) == sorted(task.order.items)
    assert shown["items"] != list(task.order.items)
    assert "correct" not in str(shown) and "answers" not in str(shown)
    right = positions_of(shown["items"], task.order.items)
    assert judge(task, {"order": right}) and not judge(task, {"order": right[::-1]})
    assert describe(task, {"order": right}) == " → ".join(task.order.items)


def test_true_false_keeps_its_fixed_order():
    view = presentation(choice_task("true_false"))
    assert view == {
        "type": "choice",
        "version": 1,
        "variant": "true_false",
        "options": ["对", "错"],
    }
    assert judge(choice_task("true_false"), {"choice": 1})
    assert not judge(choice_task("true_false"), {"choice": 0})


def test_match_and_choice_and_fill_judging():
    choice = choice_task("single")
    shown = presentation(choice)["options"]
    assert judge(choice, {"choice": shown.index("b")})
    assert not judge(choice, {"choice": shown.index("a")})
    assert describe(choice, {"choice": shown.index("b")}) == "b"

    match = match_task().model_copy(
        update={
            "match": MatchContent(
                left=("bends", "folds", "smiles"), right=("弯了", "折", "微笑"), distractors=("掉",)
            )
        }
    )
    view = presentation(match)
    assert view["left"] == ["bends", "folds", "smiles"] and sorted(view["right"]) == sorted(
        ("弯了", "折", "微笑", "掉")
    )
    right = positions_of(view["right"], ("弯了", "折", "微笑"))
    assert judge(match, {"pairs": right})
    assert not judge(match, {"pairs": [right[1], right[0], right[2]]})
    assert describe(match, {"pairs": right}) == "bends = 弯了；folds = 折；smiles = 微笑"

    bank = fill_task().model_copy(
        update={
            "fill": FillContent(
                variant="word_bank",
                text="She ____ her car on the paper.",  # Four underscores still mark one blank.
                answers=(("puts", "put"),),
                distractors=("folds", "draws"),
            )
        }
    )
    view = presentation(bank)
    assert view["text"] == "She ___ her car on the paper." and view["blanks"] == 1
    assert sorted(view["bank"]) == ["draws", "folds", "puts"]
    assert judge(bank, {"blanks": ["put"]}) and not judge(bank, {"blanks": ["folds"]})
    assert presentation(fill_task())["bank"] is None


def test_fill_ignores_case_spacing_and_end_punctuation_only():
    task = fill_task().model_copy(
        update={
            "fill": FillContent(
                variant="free",
                text="She ___ her car on the paper.",
                answers=(("puts",),),
                distractors=(),
            )
        }
    )
    assert judge(task, {"blanks": [" Puts. "]}) and not judge(task, {"blanks": ["put"]})
    assert describe(task, {"blanks": ["puts"]}) == "puts"


@pytest.mark.parametrize(
    ("make", "response"),
    [
        (choice_task, {"choice": 9}),
        (choice_task, {"choice": "a"}),
        (choice_task, {"choice": True}),
        (choice_task, {}),
        (choice_task, {"choice": 0, "extra": 1}),
        (order_task, {"order": [0, 0, 1, 2]}),
        (order_task, {"order": [0, 1, 2]}),
        (match_task, {"pairs": [0]}),
        (match_task, {"pairs": [0, 0, 1]}),
        (fill_task, {"blanks": []}),
        (fill_task, {"blanks": ["  "]}),
        (fill_task, {"blanks": [3]}),
    ],
)
def test_malformed_responses_are_refused(make, response):
    with pytest.raises(ValueError, match="作答格式不对"):
        judge(make(), response)


def test_shuffle_is_stable_and_never_the_original_order():
    assert shuffled("t1", 4) == shuffled("t1", 4) and shuffled("t1", 4) != (0, 1, 2, 3)
    assert all(shuffled(f"task-{i}", 3) != (0, 1, 2) for i in range(50))
    assert sorted(shuffled("t2", 6)) == list(range(6)) and shuffled("t3", 1) == (0,)


def test_answer_tasks_have_no_page_view():
    assert presentation(definition()) is None


def test_every_task_type_is_registered_in_preparation_classroom_and_evaluation():
    purposes = set(get_args(TaskDefinition.model_fields["purpose"].annotation))
    assert set(SCORING) == purposes
    assert {code for code, basis in SCORING.items() if basis == "rule"} == PROGRAM_JUDGED
    assert PROGRAM_JUDGED == set(JUDGES)
    assert set(TYPE_INSTRUCTIONS) == set(LADDERS) == PROGRAM_JUDGED | {"open"}
