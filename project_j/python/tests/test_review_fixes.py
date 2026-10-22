"""Regressions for the P1 independent review findings (simulation and temporary stores only)."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from support import (
    LEARNER,
    complete_story,
    confirmed,
    deliver_all,
    next_course,
    prepare_course,
    read_story,
    ready,
    start,
    teach,
    wait_jobs,
)

from project_j.application.configuration import Settings
from project_j.application.documents import identity
from project_j.application.progress import advance, next_step
from project_j.classroom.harness import DeliveryFeedback
from project_j.contracts import RevisionRef
from project_j.curriculum.content import check_content
from project_j.interface.api import create_app
from project_j.learning.adoption import adopt_state
from project_j.learning.contracts import DemandBody
from project_j.learning.maintenance import review
from project_j.platform.storage import Conflict
from project_j.preparation.contracts import CourseBody


def ref(value):
    return RevisionRef.model_validate(value)


async def first_lesson_with_next_course(app, helped=False):
    core, fact = await complete_story(app, helped=helped)
    app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
    result = await next_course(app, "next", LEARNER, core.activity_id)
    return core, fact, result


def test_course_template_and_mapping_follow_the_saved_demand(application):
    app = application
    first = app.store.read(ref(asyncio.run(app.prepare("first", LEARNER))["course_ref"]), LEARNER)
    assert first.body["tasks"][0]["prompt"] == "What does Mia want to make?"

    async def scenario():
        _, _, result = await first_lesson_with_next_course(app)
        demand = app.current(LEARNER, "demand")
        again = app.store.read(ref((await app.prepare("again", LEARNER))["course_ref"]), LEARNER)
        # The current D2 decides the new-material template; no caller flag can select Mia again.
        assert again.body["demand_ref"] == demand.ref.model_dump()
        assert again.body["tasks"][0]["prompt"] == "Which bus did Ben try first?"
        course = CourseBody.model_validate(again.body)
        material = app.store.read(course.material_refs[0], LEARNER)
        incomplete = course.model_copy(update={"demand_mapping": course.demand_mapping[1:]})
        with pytest.raises(ValueError, match="demand items"):
            check_content(incomplete, DemandBody.model_validate(demand.body), material, app.files)
        choice = next(
            item for item in again.body["demand_mapping"] if item["field"] == "suggestion_choices"
        )
        assert choice["implementation"].startswith("Suggestion adopted")

    asyncio.run(scenario())


def test_two_fixed_courses_alternate_and_label_familiar_material(application):
    app = application
    mia, ben = "What does Mia want to make?", "Which bus did Ben try first?"
    answers = {mia: ("a bridge", "She folded it."), ben: ("the small bus", "it fell")}

    async def scenario():
        course_ref, seen = (await app.prepare("c1", LEARNER))["course_ref"], []
        for number in range(1, 5):
            course = app.store.read(ref(course_ref), LEARNER).body
            prompt = course["tasks"][0]["prompt"]
            seen.append((prompt, course["lesson_type"], course["parent_preview"]["story"]))
            fact = await teach(app, f"start{number}", course_ref, answers[prompt])
            opening = next(a.text for a in app.core.actions.values() if a.kind == "goal")
            # Jane does not present an already-read story as a first reading.
            assert ("read this story before" in opening) == (course["lesson_type"] == "review")
            activity = fact.body["activity_id"]
            app.feedback(f"feedback{number}", LEARNER, activity, "无额外补充")
            result = await next_course(app, f"next{number}", LEARNER, activity)
            course_ref = result["next_package"]["course_ref"]
            demand = app.store.read(ref(result["adoption"]["demand_ref"]), LEARNER).body
            sampling = demand["sampling_requirements"][0]
            # D2 is the only new-material observation; from D3 on both stories were already read.
            assert ("read before" in sampling) == (number >= 2)
            assert ("not a first look at new material" in sampling) == (number >= 2)
        assert seen == [
            (mia, "observation", "The Paper Bridge (first lesson)"),
            (ben, "practice", "Ben’s Bridge (new material)"),
            (mia, "review", "The Paper Bridge (read before, review)"),
            (ben, "review", "Ben’s Bridge (read before, review)"),
        ]

    asyncio.run(scenario())


def test_state_merges_all_tasks_of_a_dimension_independent_of_order(application):
    app = application

    async def scenario():
        core, _ = await complete_story(app, helped=True)
        app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        evaluation = await app.evaluate("evaluate", LEARNER, core.activity_id, "final_feedback")
        current = app.current(LEARNER, "state")
        merged = adopt_state(current, evaluation)
        reversed_evaluation = evaluation.model_copy(
            update={"body": evaluation.body | {"tasks": evaluation.body["tasks"][::-1]}}
        )
        reading = {item.dimension_id: item for item in merged.dimensions}["english.reading.facts"]
        assert (reading.judgment, reading.help) == ("needs_help", "scaffold")
        assert "reading-fact" in reading.task_scope and "reading-sequence" in reading.task_scope
        assert adopt_state(current, reversed_evaluation).dimensions == merged.dimensions

    asyncio.run(scenario())


def test_feedback_correction_limits_derived_course_until_it_is_rechecked(application):
    app = application

    async def scenario():
        core, fact, result = await first_lesson_with_next_course(app)
        app.feedback(
            "correction",
            LEARNER,
            core.activity_id,
            "模拟家长补充：R1曾经代答。",
            corrections=({"task_id": "reading-fact", "note": "家长代答"},),
        )
        restricted = {
            app.store.read(ref(doc.body["target_ref"]), LEARNER).kind
            for doc in app.store.list_current(LEARNER, "restriction")
        }
        assert {"evaluation", "state", "demand", "course"} <= restricted
        assert "fact" not in restricted and "feedback" not in restricted
        old_course = ref(result["next_package"]["course_ref"])
        assert (
            app.start("old-c2", LEARNER, old_course, materials_ready=True)["decision"] == "blocked"
        )
        assert app.store.read(fact.ref, LEARNER).body == fact.body
        rechecked = await next_course(app, "recheck", LEARNER, core.activity_id)
        new_course = ref(rechecked["next_package"]["course_ref"])
        await ready(app, {"course_ref": new_course})
        assert app.start("new-c3", LEARNER, new_course, materials_ready=True)["decision"] == (
            "allow_simulation"
        )

    asyncio.run(scenario())


def restricted_in_use(app):
    """Active restrictions on a version that is still the current one of its object."""
    return [
        doc
        for doc in app.store.list_current(LEARNER, "restriction")
        if doc.body["restriction_state"] == "active"
        and app.store.current(doc.body["target_ref"]["object_id"], LEARNER).ref
        == ref(doc.body["target_ref"])
    ]


async def jobs_done(app):
    """What the service does after each page load: start the next AI job, wait for all."""
    advance(app, LEARNER)
    await wait_jobs(app)


def test_a_correction_after_acknowledgement_is_rechecked_and_released(application, caplog):
    app = application

    async def scenario():
        core, _, _ = await first_lesson_with_next_course(app)
        activity = core.activity_id
        app.feedback(
            "correction",
            LEARNER,
            activity,
            "R1其实是看了答案才答的。",
            corrections=({"note": "R1看了答案才答"},),
        )
        assert restricted_in_use(app)
        # The course planned before the correction is not offered while it is rechecked.
        assert next_step(app, LEARNER)["kind"] != "course"
        await jobs_done(app)
        step = next_step(app, LEARNER)
        assert step["kind"] == "evaluation" and step["subject"]["activity_id"] == activity
        assert step["subject"]["corrected"] is True
        app.acknowledge("ack-again", LEARNER, activity, ref(step["subject"]["evaluation_ref"]))
        with caplog.at_level(logging.INFO, logger="project_j"):
            await jobs_done(app)
        assert not restricted_in_use(app)
        resolved = [
            doc
            for doc in app.store.list_current(LEARNER, "restriction")
            if doc.body["restriction_state"] == "resolved"
        ]
        assert resolved and all(doc.body["recheck_evidence"] for doc in resolved)
        assert f"correction rechecked activity={activity} released=" in caplog.text

    asyncio.run(scenario())


def test_a_correction_of_an_older_lesson_is_evaluated_again_and_rechecked(application, caplog):
    app = application

    async def scenario():
        first, second, _ = await correct_first_lesson_after_a_second(app)
        later = app.store.current(identity(second.body["activity_id"], "evaluation"), LEARNER)
        await jobs_done(app)
        step = next_step(app, LEARNER)
        assert step["kind"] == "evaluation" and step["subject"]["activity_id"] == first.activity_id
        assert step["subject"]["corrected"] is True
        app.acknowledge(
            "ack-c1", LEARNER, first.activity_id, ref(step["subject"]["evaluation_ref"])
        )
        with caplog.at_level(logging.INFO, logger="project_j"):
            await jobs_done(app)
        assert not restricted_in_use(app)
        assert f"learning recheck activity={first.activity_id} state_revision=" in caplog.text
        assert f"correction rechecked activity={first.activity_id} released=" in caplog.text
        # The later lesson's own evaluation stays; the next lesson is not planned from C1.
        current = app.store.current(identity(second.body["activity_id"], "evaluation"), LEARNER)
        assert current.ref == later.ref
        step = next_step(app, LEARNER)
        assert step["kind"] == "course"
        course = ref(step["subject"]["course_ref"])
        await ready(app, {"course_ref": course})
        assert app.start("after-recheck", LEARNER, course, materials_ready=True)["decision"] == (
            "allow_simulation"
        )
        app.core.control("cleanup-stop", "stop")
        app.seal()

    asyncio.run(scenario())


def test_the_dashboard_carries_the_version_a_rewritten_evaluation_replaced(application):
    app = application

    async def evaluated():
        core, _ = await complete_story(app)
        activity = core.activity_id
        return activity, await app.evaluate("first", LEARNER, activity, "after_class")

    activity, first = asyncio.run(evaluated())
    assert app.dashboard(LEARNER)["previous_evaluations"] == []
    note = {"note": "看了答案"}
    app.feedback("objection", LEARNER, activity, "R1我看了答案。", corrections=(note,))
    second = asyncio.run(app.evaluate("second", LEARNER, activity, "after_class"))
    assert second.revision == first.revision + 1
    previous = app.dashboard(LEARNER)["previous_evaluations"]
    assert [(doc["object_id"], doc["revision"]) for doc in previous] == [
        (first.object_id, first.revision)
    ]
    assert previous[0]["body"] == first.body


def test_evaluation_reuse_requires_the_same_sealed_fact_revision(application):
    app = application

    def calls():
        return len([call for call in app.models.calls if call["purpose"] == "evaluation"])

    async def scenario():
        core = await start(app)
        deliver_all(core)
        read_story(app.core)
        await app.adapter.text("speech", "a car")
        unseen = core.actions[core.pending]  # The correction was never presented before stop.
        core.control("stop", "stop")
        app.seal()
        app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        await app.evaluate("first", LEARNER, core.activity_id, "final_feedback")
        assert calls() == 1
        late = DeliveryFeedback(
            delivery_event_id="late",
            delivery_id="late",
            action_id=unseen.action_id,
            activity_id=core.activity_id,
            task_instance_id=unseen.task_instance_id,
            sequence=1,
            output_device_id="explicit_simulation_device",
            modality="text",
            status="completed",
            delivered_range=("text_content",),
            occurred_at=datetime.now(UTC),
        )
        core.delivery(late)
        app.late_delivery(late)
        revised = await app.evaluate("after-revision", LEARNER, core.activity_id, "final_feedback")
        assert calls() == 2
        assert revised.body["fact_ref"]["revision"] == 2

    asyncio.run(scenario())


def test_one_evaluation_object_per_activity_and_review_uses_only_the_latest(application):
    app = application

    async def scenario():
        core, _ = await complete_story(app)
        provisional = await app.evaluate("needed", LEARNER, core.activity_id, "after_class")
        app.feedback("feedback", LEARNER, core.activity_id, "模拟：家长提供过提示。")
        final = await app.evaluate("final", LEARNER, core.activity_id, "final_feedback")
        assert final.object_id == provisional.object_id and final.supersedes == provisional.ref
        assert [doc.ref for doc in app.store.list_current(LEARNER, "evaluation")] == [final.ref]
        saved = await review(app, "week-review", LEARNER, "week")
        body = app.store.read(ref(saved["review_ref"]), LEARNER).body
        assert body["evidence_refs"] == [final.ref.model_dump()]

    asyncio.run(scenario())


def test_class_control_takes_every_command_for_the_current_activity(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        auth = {"Authorization": f"Bearer {app.token}"}
        prepared = prepare_course(app, "p")
        confirmed(app, prepared)
        app.start(
            "s", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), materials_ready=True
        )
        paused = client.post(
            "/api/class/control", headers=auth, json={"event_id": "p1", "command": "pause"}
        )
        assert paused.json()["mode"] == "paused"
        activity = paused.json()["activity_id"]
        other = client.post(
            "/api/class/control",
            headers=auth,
            json={"event_id": "r0", "activity_id": "another", "command": "resume"},
        )
        assert other.status_code == 409
        resumed = client.post(
            "/api/class/control",
            headers=auth,
            json={"event_id": "r1", "activity_id": activity, "command": "resume"},
        )
        assert resumed.status_code == 200 and resumed.json()["mode"] == "delivering"
        client.post("/api/class/control", headers=auth, json={"event_id": "s1", "command": "stop"})


def test_failed_save_records_next_step_and_deadline_then_resumes(application):
    app = application
    original = app.save_step

    def fail_package(*args, **kwargs):
        if args[2] == "package":
            app.store.fail_at = "before_commit"
        return original(*args, **kwargs)

    app.save_step = fail_package
    with pytest.raises(OSError):
        asyncio.run(app.prepare("prepare-failure", LEARNER))
    app.save_step = original
    operation = app.request(
        "prepare-failure",
        LEARNER,
        "prepare",
        {"demand_ref": app.current(LEARNER, "demand").ref.model_dump()},
        sources=(app.current(LEARNER, "demand").ref,),
        baselines=tuple(
            app.current(LEARNER, label).ref for label in ("demand", "state", "plan:week", "profile")
        ),
    )
    steps = {row["step_id"]: row["payload"] for row in app.ledger.steps(operation, LEARNER)}
    failed = steps["package"]
    assert (failed["execution"], failed["save"], failed["next_step"]) == (
        "failed",
        "failed",
        "package",
    )
    assert failed["deadline"] and failed["failure"] == "OSError"
    asyncio.run(app.prepare("prepare-failure", LEARNER))
    steps = {row["step_id"]: row["payload"] for row in app.ledger.steps(operation, LEARNER)}
    assert steps["package"]["execution"] == "complete"


def test_lost_save_acknowledgement_is_queried_before_reporting_failure(application):
    app = application
    app.store.fail_at = "after_commit"
    prepared = asyncio.run(app.prepare("lost-ack", LEARNER))
    assert app.store.read(ref(prepared["course_ref"]), LEARNER).kind == "course"


def test_seal_progress_database_error_keeps_the_durable_fact(application, monkeypatch):
    app = application
    core = asyncio.run(start(app))
    core.control("stop", "stop")

    def locked(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("database is locked"))

    monkeypatch.setattr(app.ledger, "accept", locked)
    fact = app.seal()
    assert app.activity_facts(core.activity_id, LEARNER).ref == fact.ref


def test_successful_seal_retry_clears_the_reported_error(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        parent = {"Authorization": f"Bearer {app.token}"}
        prepared = prepare_course(app, "p")
        confirmed(app, prepared)
        app.start(
            "s", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), materials_ready=True
        )
        app.store.fail_at = "before_commit"
        client.post(
            "/api/class/control",
            headers={"Authorization": f"Bearer {app.token}"},
            json={"event_id": "stop", "command": "stop"},
        )
        assert client.get("/api/dashboard", headers=parent).json()["seal_errors"]
        assert client.post("/api/retry-seal", headers=parent).status_code == 200
        dashboard = client.get("/api/dashboard", headers=parent).json()
        assert dashboard["seal_errors"] == {} and dashboard["pending_seals"] == []


def test_inapplicable_plan_proposal_is_rejected_before_any_decision(application):
    app = application
    with pytest.raises(ValueError, match="30 minutes"):
        asyncio.run(review(app, "too-long", LEARNER, "week", {"budget_minutes": 45}))
    assert not app.store.list_current(LEARNER, "decision")


def test_replayed_start_of_an_ended_activity_never_returns_its_old_result(application):
    app = application
    course = ref(asyncio.run(app.prepare("prepare", LEARNER))["course_ref"])
    confirmed(app, {"course_ref": course})
    app.start("start-A", LEARNER, course, materials_ready=True)
    app.core.control(str(uuid4()), "stop")
    app.seal()
    app.start("start-B", LEARNER, course, materials_ready=True)
    with pytest.raises(Conflict, match="original start ended"):
        app.start("start-A", LEARNER, course, materials_ready=True)


async def correct_first_lesson_after_a_second(app):
    """C1 with help, C2 taught and followed by C3, then a correction to C1's first task."""
    first, _, result = await first_lesson_with_next_course(app, helped=True)
    second = await teach(
        app, "start2", result["next_package"]["course_ref"], ("the small bus", "it fell")
    )
    activity = second.body["activity_id"]
    app.feedback("feedback2", LEARNER, activity, "无额外补充")
    third = await next_course(app, "next2", LEARNER, activity)
    app.feedback(
        "correction1",
        LEARNER,
        first.activity_id,
        "C1第一题家长代答。",
        corrections=({"task_id": "reading-fact", "note": "家长代答，不能按儿童独立样本解释。"},),
    )
    return first, second, third


def test_E2E06_correction_limits_only_the_activity_and_current_derived_objects(application):
    app = application

    async def scenario():
        first, second, third = await correct_first_lesson_after_a_second(app)
        targets = {
            (doc.body["target_ref"]["object_id"], doc.body["target_ref"]["revision"])
            for doc in app.store.list_current(LEARNER, "restriction")
            if doc.body["restriction_state"] == "active"
        }
        restricted = {object_id for object_id, _ in targets}
        current = [app.current(LEARNER, kind).ref for kind in ("state", "demand")]
        assert {(ref.object_id, ref.revision) for ref in current} <= targets
        assert identity(first.activity_id, "evaluation") in restricted
        assert identity(first.activity_id, "suggestion") in restricted
        assert third["next_package"]["course_ref"]["object_id"] in restricted
        # Later evidence and lessons that already happened are not uses of the corrected source.
        assert identity(second.body["activity_id"], "evaluation") not in restricted
        assert second.body["course_ref"]["object_id"] not in restricted

    asyncio.run(scenario())


def test_E2E06_rechecking_an_older_lesson_keeps_newer_evidence_and_the_lesson_order(application):
    app = application

    async def scenario():
        first, second, _ = await correct_first_lesson_after_a_second(app)
        result = await next_course(app, "recheck1", LEARNER, first.activity_id)
        state = app.store.read(ref(result["adoption"]["state_ref"]), LEARNER)
        dims = {item["dimension_id"]: item for item in state.body["dimensions"]}
        later = app.store.current(identity(second.body["activity_id"], "evaluation"), LEARNER)
        reading = dims["english.reading.facts"]
        assert reading["evidence_refs"] == [later.ref.model_dump()]
        assert any("An earlier class" in item for item in reading["limitations"])
        # English expression was only observed in C1, so its corrected source is re-reviewed.
        rechecked = app.store.current(identity(first.activity_id, "evaluation"), LEARNER)
        speaking = dims["english.speaking.report"]
        assert speaking["judgment"] == "unknown"
        assert speaking["evidence_refs"] == [rechecked.ref.model_dump()]
        # The next lesson still follows the latest one (C2 Ben), not the corrected C1.
        course = app.store.read(ref(result["next_package"]["course_ref"]), LEARNER).body
        assert course["tasks"][0]["prompt"] == "What does Mia want to make?"
        assert course["lesson_type"] == "review"

    asyncio.run(scenario())
