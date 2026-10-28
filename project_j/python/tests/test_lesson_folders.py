"""ADR-0027: confirming the resources makes the lesson folder; classes read only from it."""

import asyncio
import json
import logging
from datetime import date

import pytest
from support import LEARNER
from test_confirm_and_start import end_class
from test_preparation_resources import prepared

from project_j.platform.files import file_stem
from project_j.platform.storage import Conflict
from project_j.preparation import lessons
from project_j.preparation.folders import draft_of, local_today
from project_j.preparation.lessons import confirm_resources, lesson_of, remake_broken
from project_j.preparation.resources import (
    choose_resource,
    ready_resources,
    remake_resource,
    resource_file,
    resources_of,
)


def made(application):
    course_ref = prepared(application)
    application.confirm("c1", LEARNER, course_ref)
    asyncio.run(ready_resources(application, LEARNER, course_ref))
    return course_ref


def demo_id(application, course_ref):
    items = resources_of(application, LEARNER, course_ref)
    return next(i["resource_id"] for i in items if i["resource_id"].startswith("demo:"))


def test_confirming_resources_makes_the_lesson_folder(application, caplog):
    course_ref = made(application)
    demo = demo_id(application, course_ref)
    text = next(
        i for i in resources_of(application, LEARNER, course_ref) if i["resource_id"] == demo
    )
    asyncio.run(remake_resource(application, "m1", LEARNER, course_ref, demo))
    asyncio.run(remake_resource(application, "m2", LEARNER, course_ref, demo))
    asyncio.run(choose_resource(application, "u2", LEARNER, course_ref, demo, 2))
    chosen, _ = resource_file(application, LEARNER, course_ref, demo, 2)
    draft = draft_of(application.store, LEARNER, course_ref.object_id)
    caplog.set_level(logging.INFO, logger="project_j")
    result = confirm_resources(application, "r1", LEARNER, course_ref)
    folder = f"lessons/lesson-001-{local_today().isoformat()}"
    assert (result["folder"], result["number"]) == (folder, 1)
    run = application.store.run
    assert {"story.md", "course.json", "index.md", "audio"} <= {
        p.name for p in (run / folder).iterdir()
    }
    lesson = lesson_of(application, LEARNER, course_ref)
    items = resources_of(application, LEARNER, course_ref)
    assert sorted(lesson.body["files"]) == sorted(
        i["resource_id"] for i in items if i.get("adopted_version")
    )
    for entry in lesson.body["files"].values():
        assert entry["path"].startswith(f"{folder}/") and ".v" not in entry["path"]
        application.files.read_binary(entry)  # Each file matches its record.
    application.files.read_asset(lesson.body["story"])
    item = lesson.body["files"][demo]
    assert item["path"] == f"{folder}/audio/{file_stem(demo)}.wav"
    assert application.files.read_binary(item) == chosen
    assert not (run / draft).exists()
    assert text["text"] in (run / folder / "index.md").read_text()
    assert json.loads((run / folder / "course.json").read_text())["course"]["tasks"]
    again = confirm_resources(application, "r2", LEARNER, course_ref)
    assert (again["folder"], again["number"]) == (folder, 1)
    with pytest.raises(Conflict, match="locked"):
        asyncio.run(remake_resource(application, "m3", LEARNER, course_ref, demo))
    with pytest.raises(Conflict, match="locked"):
        asyncio.run(choose_resource(application, "u3", LEARNER, course_ref, demo, 1))
    assert f"lesson confirmed course={course_ref.object_id} category=lesson number=1" in caplog.text
    assert f"lesson drafts removed course={course_ref.object_id}" in caplog.text
    assert text["text"] not in caplog.text


def test_a_retry_after_the_files_keeps_one_folder_and_number(application, monkeypatch):
    course_ref = made(application)
    run = application.store.run
    folder = f"lessons/lesson-001-{local_today().isoformat()}"
    application.store.fail_at = "lesson:before_commit"
    with pytest.raises(OSError):
        confirm_resources(application, "r1", LEARNER, course_ref)
    assert (run / folder / "story.md").exists() and lesson_of(
        application, LEARNER, course_ref
    ) is None
    monkeypatch.setattr(lessons, "local_today", lambda: date(2099, 1, 1))  # Past midnight.
    result = confirm_resources(application, "r1", LEARNER, course_ref)
    assert (result["folder"], result["number"]) == (folder, 1)
    assert [path.name for path in (run / "lessons").iterdir()] == [folder.split("/")[1]]


def test_start_reads_only_the_lesson_folder(application):
    course_ref = made(application)
    with pytest.raises(Conflict, match="Confirm the resources first"):
        application.start("s0", LEARNER, course_ref, materials_ready=True)
    confirm_resources(application, "r1", LEARNER, course_ref)
    started = application.start("s1", LEARNER, course_ref, materials_ready=True)
    assert started["decision"] == "allow_simulation"
    assert application.recorded  # Adopted speech came from the lesson folder.


def test_start_before_confirming_resources_confirms_them(application):
    course_ref = prepared(application)
    result = asyncio.run(application.confirm_and_start("one", LEARNER, course_ref, True))
    assert result["decision"] == "allow_simulation"
    end_class(application)  # Storage stays closed while the class runs.
    folder = lesson_of(application, LEARNER, course_ref).body["folder"]
    assert folder.startswith("lessons/lesson-001-")


def test_a_broken_file_is_remade_into_the_same_folder(application, caplog):
    course_ref = made(application)
    result = confirm_resources(application, "r1", LEARNER, course_ref)
    lesson = lesson_of(application, LEARNER, course_ref)
    demo = demo_id(application, course_ref)
    others = {rid: item["sha256"] for rid, item in lesson.body["files"].items() if rid != demo}
    (application.store.run / lesson.body["files"][demo]["path"]).write_bytes(b"broken")
    with pytest.raises(Conflict, match="Resources not usable"):
        application.start("s1", LEARNER, course_ref, materials_ready=True)
    caplog.set_level(logging.INFO, logger="project_j")
    remade = asyncio.run(remake_broken(application, "b1", LEARNER, course_ref))
    assert remade["remade"] == [demo]
    after = lesson_of(application, LEARNER, course_ref)
    assert after.revision == lesson.revision + 1
    assert (after.body["folder"], after.body["number"]) == (result["folder"], result["number"])
    kept = {rid: item["sha256"] for rid, item in after.body["files"].items() if rid != demo}
    assert kept == others
    application.files.read_binary(after.body["files"][demo])
    assert f"lesson remade course={course_ref.object_id} resources=1" in caplog.text
    assert asyncio.run(remake_broken(application, "b2", LEARNER, course_ref))["remade"] == []
    started = application.start("s2", LEARNER, course_ref, materials_ready=True)
    assert started["decision"] == "allow_simulation"
