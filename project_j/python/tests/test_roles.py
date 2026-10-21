"""ADR-0018: the program knows one role, the student; no child, parent or adult check."""

import asyncio

import pytest
from support import LEARNER

from project_j.classroom import judgment
from project_j.evaluation import assessment
from project_j.learning import planning
from project_j.learning.contracts import normalize_lexile
from project_j.preparation import drafting, materials

ROLES = ("孩子", "家长", "儿童", "成人")


def test_model_instructions_name_no_role():
    for text in (
        planning.PLANNING_INSTRUCTIONS,
        planning.REVIEW_INSTRUCTIONS,
        drafting.PREPARATION_INSTRUCTIONS,
        drafting.DISCUSSION_INSTRUCTIONS,
        assessment.EVALUATION_INSTRUCTIONS,
        judgment.INSTRUCTIONS,
        materials.IMAGE_INSTRUCTIONS,
        materials.ANIMATION_INSTRUCTIONS,
    ):
        for word in ROLES:
            assert word not in text, word


def test_prepared_course_and_evaluation_name_no_role(application):
    from support import complete_story

    core, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "无额外补充", [])
    asyncio.run(application.evaluate("e1", LEARNER, activity, "next_course"))
    for kind in ("course", "brief", "preview", "evaluation"):
        for doc in application.store.list_current(LEARNER, kind):
            text = str(doc.body)
            for word in ROLES:
                assert word not in text, (kind, word)


def test_lexile_is_normalized_or_refused():
    assert normalize_lexile(" 650l ") == "650L"
    assert normalize_lexile("br100L") == "BR100L"
    assert normalize_lexile("1200L") == "1200L"
    for bad in ("650", "L", "abcL", "12345L", "", "BRL"):
        with pytest.raises(ValueError):
            normalize_lexile(bad)
