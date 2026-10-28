"""ADR-0027: a course's story versions live in its draft; drafts of courses never confirmed go."""

import asyncio
import logging
import re

from support import LEARNER
from test_preparation_model import FEEDBACK, ref, science_lesson

from project_j.preparation.folders import draft_of, next_number


def test_story_versions_live_in_the_draft_until_the_content_is_confirmed(application, monkeypatch):
    first = science_lesson(application, monkeypatch)
    draft = draft_of(application.store, LEARNER, first.object_id)
    assert re.fullmatch(r"drafts/\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", draft)
    second = ref(asyncio.run(application.discuss("d1", LEARNER, first, FEEDBACK))["course_ref"])
    run = application.store.run
    assert (run / draft / "story.r1.md").exists() and (run / draft / "story.r2.md").exists()
    course = application.store.read(second, LEARNER)
    material = application.store.read(ref(course.body["material_refs"][0]), LEARNER)
    assert material.body["asset"]["path"] == f"{draft}/story.r2.md"
    application.confirm("c1", LEARNER, second)
    assert not (run / draft / "story.r1.md").exists() and (run / draft / "story.r2.md").exists()


def test_a_new_course_removes_drafts_of_other_courses(application, caplog):
    application.files.put("drafts/2026-01-01-deadbeef/story.r1.md", "Old")
    caplog.set_level(logging.INFO, logger="project_j")
    course_ref = ref(asyncio.run(application.prepare("p1", LEARNER))["course_ref"])
    draft = draft_of(application.store, LEARNER, course_ref.object_id)
    assert not (application.store.run / "drafts/2026-01-01-deadbeef").exists()
    assert (application.store.run / draft).exists()
    asyncio.run(application.prepare("p2", LEARNER))  # Same demand: the same course, kept.
    assert (application.store.run / draft).exists()
    assert "drafts removed count=1" in caplog.text and "Old" not in caplog.text


def test_lesson_numbers_count_per_category_without_gaps():
    done = [
        {"category": "lesson", "number": 1},
        {"category": "placement", "number": 1},
        {"category": "lesson", "number": 2},
    ]
    assert next_number(done, "lesson") == 3 and next_number(done, "placement") == 2
    assert next_number([], "lesson") == 1
