"""Making, checking, adopting or turning down the pictures that are checked (ADR-0026): every
picture of a picture question together, or one still story picture."""

import asyncio
import logging

import pytest
from support import LEARNER
from test_picture_drafting import OPTIONS, STYLE, picture_draft
from test_preparation_model import science_lesson

from project_j.platform.models.contracts import AdapterReply
from project_j.platform.storage import Conflict
from project_j.preparation import resources as made
from project_j.preparation.lessons import confirm_resources, lesson_of, remake_broken
from project_j.preparation.pictures import picture_groups
from project_j.preparation.resources import (
    choose_resource,
    generate_resource,
    items_by_id,
    ready_resources,
    remake_resource,
)

OPTION_IDS = [f"cup-option-{i}" for i in range(len(OPTIONS))]


def lesson(application, monkeypatch):
    """The picture lesson, with the model calls counted: pictures drawn and checks made."""
    course_ref = science_lesson(application, monkeypatch, picture_draft)
    adapter = application.models.adapters["simulated"]
    original = adapter.invoke
    counted = {"drawn": 0, "checks": 0}

    async def invoke(request, binding):
        if request.media == "image":
            counted["drawn"] += 1
        if request.context.get("kind") == "check":
            counted["checks"] += 1
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)
    return course_ref, adapter, counted


def confirm(application, course_ref, request_id="c1"):
    return asyncio.run(application.confirm_course(request_id, LEARNER, course_ref))


def items(application, course_ref) -> dict[str, dict]:
    return items_by_id(application, LEARNER, course_ref)


def versions(item) -> int:
    return len(item.get("versions", ()))


def generate(application, course_ref, resource_id, request_id):
    return asyncio.run(generate_resource(application, request_id, LEARNER, course_ref, resource_id))


def choose(application, course_ref, resource_id, version, request_id):
    return asyncio.run(
        choose_resource(application, request_id, LEARNER, course_ref, resource_id, version)
    )


def remake(application, course_ref, resource_id, request_id):
    return asyncio.run(remake_resource(application, request_id, LEARNER, course_ref, resource_id))


def failing(adapter, monkeypatch, when):
    """Model calls for which when(request) holds fail as unavailable."""
    original = adapter.invoke

    async def invoke(request, binding):
        if when(request):
            return AdapterReply(data=None, failure="unavailable")
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)


PASS = {"result": "pass", "reason": None}


def test_illustration_is_checked_then_adopted(application, monkeypatch):
    course_ref, _, _ = lesson(application, monkeypatch)
    confirm(application, course_ref)
    scene = items(application, course_ref)["cup-scene"]
    assert scene["adopted_version"] == 1 and scene["checks"] == {"1": PASS}


def test_animation_illustration_is_not_checked(application, monkeypatch):
    course_ref, _, counted = lesson(application, monkeypatch)
    confirm(application, course_ref)
    motion = items(application, course_ref)["kitchen-motion"]
    assert motion["adopted_version"] == 1 and "checks" not in motion
    assert counted["checks"] == 3  # The still story picture and the two picture questions.


def test_only_failing_option_pictures_are_redrawn(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}]  # The story picture passes; option 2 fails once.
    confirm(application, course_ref)
    found = items(application, course_ref)
    assert [versions(found[name]) for name in OPTION_IDS] == [1, 2, 1]
    assert [found[name]["adopted_version"] for name in OPTION_IDS] == [1, 2, 1]
    assert found["cup-option-1"]["checks"] == {
        "1": {"result": "fail", "reason": "wrong"},
        "2": PASS,
    }


def test_option_question_falls_back_after_three_failures(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}, {2}, {2}]
    confirm(application, course_ref)  # Turning a question down blocks nothing.
    found = items(application, course_ref)
    assert [found[name]["state"] for name in OPTION_IDS] == ["rejected"] * 3
    assert {found[name]["rejected_reason"] for name in OPTION_IDS} == {"wrong"}
    assert not any(found[name].get("adopted_version") for name in OPTION_IDS)
    assert [versions(found[name]) for name in OPTION_IDS] == [1, 3, 1]


def test_picture_question_failing_falls_back(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), set(), {1}, {1}, {1}]
    confirm(application, course_ref)
    look = items(application, course_ref)["cup-look"]
    assert (look["state"], look["rejected_reason"], versions(look)) == ("rejected", "wrong", 3)


def test_failing_illustration_is_not_shown(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [{1}, {1}, {1}]
    confirm(application, course_ref)
    scene = items(application, course_ref)["cup-scene"]
    assert (scene["state"], scene["rejected_reason"], versions(scene)) == ("rejected", "wrong", 3)
    confirm_resources(application, "r1", LEARNER, course_ref)
    application.start("s1", LEARNER, course_ref, materials_ready=True)
    assert "cup-scene" not in application.visuals and "kitchen-motion" in application.visuals


def test_without_image_input_task_pictures_are_not_drawn(application, monkeypatch):
    course_ref, _, counted = lesson(application, monkeypatch)
    monkeypatch.setattr(application.models, "sees_images", lambda purpose: False)
    confirm(application, course_ref)
    found = items(application, course_ref)
    for name in (*OPTION_IDS, "cup-look"):
        assert (found[name]["state"], found[name]["rejected_reason"]) == (
            "rejected",
            "no_image_input",
        )
        assert versions(found[name]) == 0
    scene = found["cup-scene"]
    assert scene["adopted_version"] == 1
    assert scene["checks"] == {"1": {"result": "unchecked", "reason": None}}
    assert counted["checks"] == 0


def test_a_failed_check_call_counts_as_unavailable(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    failing(adapter, monkeypatch, lambda request: request.context.get("kind") == "check")
    confirm(application, course_ref)
    found = items(application, course_ref)
    for name in (*OPTION_IDS, "cup-look"):
        assert (found[name]["state"], found[name]["rejected_reason"]) == (
            "rejected",
            "check_unavailable",
        )
    assert found["cup-scene"]["checks"] == {"1": {"result": "unchecked", "reason": None}}
    assert found["cup-scene"]["adopted_version"] == 1


def test_class_start_skips_rejected_and_finishes_the_rest(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}, {2}, {2}]
    broken = {"on": True}
    failing(
        adapter,
        monkeypatch,
        lambda request: broken["on"] and request.context.get("content") == "What cup-look shows.",
    )
    confirm(application, course_ref)
    before = items(application, course_ref)
    assert versions(before["cup-look"]) == 0 and before["cup-look"].get("state") != "rejected"
    broken["on"] = False
    asyncio.run(application.confirm_and_start("s1", LEARNER, course_ref, True))
    application.core.control("stop-s1", "stop")
    application.seal()
    after = items(application, course_ref)
    assert [versions(after[name]) for name in OPTION_IDS] == [1, 3, 1]
    assert after["cup-look"]["adopted_version"] == 1 and after["cup-look"]["checks"] == {"1": PASS}


def test_ready_resources_replays_after_a_restart(application, monkeypatch):
    course_ref, _, counted = lesson(application, monkeypatch)
    original = made.adopt_resource
    crashed = []

    def adopt(app, request_id, learner_id, course, resource_id, version):
        if resource_id == "cup-option-0" and not crashed:
            crashed.append(request_id)
            raise RuntimeError("the service stopped")
        return original(app, request_id, learner_id, course, resource_id, version)

    monkeypatch.setattr(made, "adopt_resource", adopt)
    with pytest.raises(RuntimeError):
        confirm(application, course_ref)
    seen = dict(counted)
    asyncio.run(ready_resources(application, LEARNER, course_ref))
    found = items(application, course_ref)
    assert [versions(found[name]) for name in OPTION_IDS] == [1, 1, 1]
    assert all(found[name]["adopted_version"] == 1 for name in OPTION_IDS)
    # Only the picture question after the crash is drawn and checked anew.
    assert counted == {"drawn": seen["drawn"] + 1, "checks": seen["checks"] + 1}


def test_remaking_a_picture_rechecks_and_adopts(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    confirm(application, course_ref)
    result = remake(application, course_ref, "cup-option-1", "m1")
    assert result["state"] == "adopted" and result["version"] == 2
    found = items(application, course_ref)
    assert [found[name]["adopted_version"] for name in OPTION_IDS] == [1, 2, 1]
    adapter.reject_pictures = [{2}]
    with pytest.raises(Conflict, match="did not pass"):
        remake(application, course_ref, "cup-option-1", "m2")
    option = items(application, course_ref)["cup-option-1"]
    assert versions(option) == 3 and option["adopted_version"] == 2


def test_remaking_restores_a_rejected_question(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}, {2}, {2}]
    confirm(application, course_ref)
    remake(application, course_ref, "cup-option-1", "m1")
    found = items(application, course_ref)
    assert all(found[name]["state"] == "adopted" for name in OPTION_IDS)
    assert not any("rejected_reason" in found[name] for name in OPTION_IDS)
    assert found["cup-option-1"]["adopted_version"] == 4


def test_remaking_a_plain_resource_only_makes_it(application, monkeypatch):
    course_ref, _, counted = lesson(application, monkeypatch)
    confirm(application, course_ref)
    checks = counted["checks"]
    result = remake(application, course_ref, "kitchen-motion", "m1")
    motion = items(application, course_ref)["kitchen-motion"]
    assert result["state"] == "generated" and motion["adopted_version"] == 1
    assert counted["checks"] == checks


def test_choosing_a_version_rechecks_the_group(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    confirm(application, course_ref)
    generate(application, course_ref, "cup-option-1", "g2")
    adapter.reject_pictures = [{2}]
    with pytest.raises(Conflict, match="did not pass"):
        choose(application, course_ref, "cup-option-1", 2, "u2")
    option = items(application, course_ref)["cup-option-1"]
    assert option["adopted_version"] == 1
    assert option["checks"]["2"] == {"result": "fail", "reason": "wrong"}
    generate(application, course_ref, "cup-option-1", "g3")
    choose(application, course_ref, "cup-option-1", 3, "u3")
    found = items(application, course_ref)
    assert [found[name]["adopted_version"] for name in OPTION_IDS] == [1, 3, 1]


def test_choosing_restores_a_rejected_question(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}, {2}, {2}]
    confirm(application, course_ref)
    generate(application, course_ref, "cup-option-1", "g4")
    choose(application, course_ref, "cup-option-1", 4, "u4")
    found = items(application, course_ref)
    assert all(found[name]["state"] == "adopted" for name in OPTION_IDS)
    assert not any("rejected_reason" in found[name] for name in OPTION_IDS)


def test_choosing_needs_every_option_picture(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    lost = {"What cup-option-1 shows.", "What cup-option-2 shows."}
    failing(adapter, monkeypatch, lambda request: request.context.get("content") in lost)
    confirm(application, course_ref)
    assert versions(items(application, course_ref)["cup-option-0"]) == 1
    with pytest.raises(Conflict, match="Make the other pictures"):
        choose(application, course_ref, "cup-option-0", 1, "u1")
    assert not any(
        items(application, course_ref)[name].get("adopted_version") for name in OPTION_IDS
    )


def test_choosing_without_image_input_keeps_words(application, monkeypatch):
    course_ref, _, _ = lesson(application, monkeypatch)
    confirm(application, course_ref)
    generate(application, course_ref, "cup-option-1", "g2")
    monkeypatch.setattr(application.models, "sees_images", lambda purpose: False)
    with pytest.raises(Conflict, match="cannot be checked"):
        choose(application, course_ref, "cup-option-1", 2, "u2")
    assert items(application, course_ref)["cup-option-1"]["adopted_version"] == 1


def test_choosing_an_illustration_version_is_checked(application, monkeypatch):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    confirm(application, course_ref)
    generate(application, course_ref, "cup-scene", "g2")
    adapter.reject_pictures = [{1}]
    with pytest.raises(Conflict, match="did not pass"):
        choose(application, course_ref, "cup-scene", 2, "u2")
    assert items(application, course_ref)["cup-scene"]["adopted_version"] == 1


def test_old_preview_items_form_no_groups():
    old = {
        "resource_id": "cup-picture",
        "kind": "image",
        "purpose": "p",
        "content": "c",
        "task_ids": ["cup-fact"],
        "audience": "student",
        "required": False,
        "fallback": "f",
        "state": "adopted",
    }
    assert picture_groups([old, old | {"resource_id": "x", "stage": "help"}]) == []


def test_picture_logs_carry_no_content(application, monkeypatch, caplog):
    course_ref, adapter, _ = lesson(application, monkeypatch)
    adapter.reject_pictures = [set(), {2}, {2}, {2}]
    caplog.set_level(logging.INFO, logger="project_j")
    confirm(application, course_ref)
    text = caplog.text
    assert (
        f"picture check course={course_ref.object_id} group=task:cup-choice "
        "resources=cup-option-0,cup-option-1,cup-option-2 round=1 result=fail reasons=wrong"
    ) in text
    assert f"picture rejected course={course_ref.object_id} group=task:cup-choice" in text
    assert f"resource generated course={course_ref.object_id} resource=cup-scene" in text
    assert f"resource adopted course={course_ref.object_id} resource=cup-scene" in text
    for secret in ("What cup-", "Which one shows", STYLE, "Simulated picture", "water"):
        assert secret not in text


def confirmed_lesson(application, monkeypatch):
    course_ref, adapter, counted = lesson(application, monkeypatch)
    confirm(application, course_ref)
    confirm_resources(application, "r1", LEARNER, course_ref)
    return course_ref, adapter, lesson_of(application, LEARNER, course_ref)


def test_a_broken_option_picture_is_remade_and_checked_with_the_others(application, monkeypatch):
    course_ref, adapter, before = confirmed_lesson(application, monkeypatch)
    files = before.body["files"]
    broken = OPTION_IDS[1]
    (application.store.run / files[broken]["path"]).write_bytes(b"broken")
    # The check reads the story: once confirmed it comes from story.md, the draft is gone.
    remade = asyncio.run(remake_broken(application, "b1", LEARNER, course_ref))
    assert remade["remade"] == [broken]
    after = lesson_of(application, LEARNER, course_ref).body["files"]
    assert {name: after[name]["sha256"] for name in files if name != broken} == {
        name: files[name]["sha256"] for name in files if name != broken
    }
    application.files.read_binary(after[broken])


def test_a_remade_option_failing_three_times_turns_the_question_to_words(application, monkeypatch):
    course_ref, adapter, before = confirmed_lesson(application, monkeypatch)
    files = before.body["files"]
    (application.store.run / files[OPTION_IDS[1]]["path"]).write_bytes(b"broken")
    adapter.reject_pictures = [{2}, {2}, {2}]
    asyncio.run(remake_broken(application, "b1", LEARNER, course_ref))
    after = lesson_of(application, LEARNER, course_ref).body
    assert set(OPTION_IDS) <= set(after["rejected"]) and not set(OPTION_IDS) & set(after["files"])
    assert not any((application.store.run / files[name]["path"]).exists() for name in OPTION_IDS)
    rest = [name for name in files if name not in OPTION_IDS]
    assert {name: after["files"][name]["sha256"] for name in rest} == {
        name: files[name]["sha256"] for name in rest
    }
    started = application.start("s1", LEARNER, course_ref, materials_ready=True)
    assert started["decision"] == "allow_simulation"
    assert not set(OPTION_IDS) & set(application.visuals)  # The question goes in words.
