"""Regressions for the P1 independent review findings (simulation and temporary stores only)."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from support import CONDITIONS, LEARNER, answer, complete_story, deliver_all, start, teach

from project_j.application.configuration import Settings
from project_j.application.documents import identity
from project_j.contracts import RevisionRef
from project_j.curriculum.content import check_content
from project_j.interface.api import create_app
from project_j.learning.adoption import adopt_state
from project_j.learning.contracts import DemandBody, LearningState
from project_j.learning.maintenance import review
from project_j.platform.storage import Conflict
from project_j.preparation.contracts import CourseBody


def ref(value):
    return RevisionRef.model_validate(value)


async def first_lesson_with_next_course(app, helped=False):
    core, fact = await complete_story(app, helped=helped)
    app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
    result = await app.next_course("next", LEARNER, core.activity_id)
    return core, fact, result


def test_course_template_and_mapping_follow_the_saved_demand(application):
    app = application
    first = app.store.read(ref(app.prepare("first", LEARNER)["course_ref"]), LEARNER)
    assert first.body["tasks"][0]["prompt"] == "What does Mia want to make?"

    async def scenario():
        _, _, result = await first_lesson_with_next_course(app)
        demand = app.current(LEARNER, "demand")
        again = app.store.read(ref(app.prepare("again", LEARNER)["course_ref"]), LEARNER)
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
        assert choice["implementation"].startswith("采纳")

    asyncio.run(scenario())


def test_two_fixed_courses_alternate_and_label_familiar_material(application):
    app = application
    mia, ben = "What does Mia want to make?", "Which bus did Ben try first?"
    answers = {mia: ("a bridge", "She folded it."), ben: ("the small bus", "it fell")}

    async def scenario():
        course_ref, seen = app.prepare("c1", LEARNER)["course_ref"], []
        for number in range(1, 5):
            course = app.store.read(ref(course_ref), LEARNER).body
            prompt = course["tasks"][0]["prompt"]
            seen.append((prompt, course["lesson_type"], course["parent_preview"]["story"]))
            fact = await teach(app, f"start{number}", course_ref, answers[prompt])
            activity = fact.body["activity_id"]
            app.feedback(f"feedback{number}", LEARNER, activity, "无额外补充")
            result = await app.next_course(f"next{number}", LEARNER, activity)
            course_ref = result["next_package"]["course_ref"]
            demand = app.store.read(ref(result["adoption"]["demand_ref"]), LEARNER).body
            sampling = demand["sampling_requirements"][0]
            # D2 is the only new-material observation; from D3 on both stories were already read.
            assert ("已读" in sampling) == (number >= 2)
            assert ("不作首次新材料观察" in sampling) == (number >= 2)
        assert seen == [
            (mia, "observation", "The Paper Bridge（首课）"),
            (ben, "practice", "Ben’s Bridge（新材料）"),
            (mia, "review", "The Paper Bridge（已读，复习）"),
            (ben, "review", "Ben’s Bridge（已读，复习）"),
        ]

    asyncio.run(scenario())


def test_state_merges_all_tasks_of_a_dimension_independent_of_order(application):
    app = application

    async def scenario():
        core, _ = await complete_story(app, helped=True)
        app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        evaluation = await app.evaluate("evaluate", LEARNER, core.activity_id, "final_feedback")
        current = LearningState.model_validate(app.current(LEARNER, "state").model_dump())
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
        assert app.start("old-c2", LEARNER, old_course, CONDITIONS)["decision"] == "blocked"
        assert app.store.read(fact.ref, LEARNER).body == fact.body
        rechecked = await app.next_course("recheck", LEARNER, core.activity_id)
        new_course = ref(rechecked["next_package"]["course_ref"])
        assert app.start("new-c3", LEARNER, new_course, CONDITIONS)["decision"] == (
            "allow_simulation"
        )

    asyncio.run(scenario())


def test_evaluation_reuse_requires_the_same_sealed_fact_revision(application):
    app = application

    def calls():
        return len([call for call in app.model.calls if call["purpose"] == "evaluation"])

    async def scenario():
        core = start(app)
        deliver_all(core)
        await answer(app, "收到", "confirmation")
        adapter = app.adapter
        await adapter.voice_event("start", "speech")
        await adapter.voice_event("candidate_end", "speech", candidate_end_ms=100)
        await adapter.voice_event("final", "speech", "a car")
        await adapter.voice_event("window_elapsed", "speech")
        deliver_all(core)
        core.control("stop", "stop")
        app.seal()
        app.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        await app.evaluate("first", LEARNER, core.activity_id, "final_feedback")
        assert calls() == 1
        await adapter.voice_event("revision", "speech", "a bridge")
        revised = await app.evaluate("after-revision", LEARNER, core.activity_id, "final_feedback")
        assert calls() == 2
        assert revised.body["fact_ref"]["revision"] == 2

    asyncio.run(scenario())


def test_one_evaluation_object_per_activity_and_review_uses_only_the_latest(application):
    app = application

    async def scenario():
        core, _ = await complete_story(app)
        app.later_feedback("later", LEARNER, core.activity_id)
        provisional = await app.evaluate("needed", LEARNER, core.activity_id, "next_course")
        app.feedback("feedback", LEARNER, core.activity_id, "模拟：家长提供过提示。")
        final = await app.evaluate("final", LEARNER, core.activity_id, "final_feedback")
        assert final.object_id == provisional.object_id and final.supersedes == provisional.ref
        assert [doc.ref for doc in app.store.list_current(LEARNER, "evaluation")] == [final.ref]
        saved = review(app, "week-review", LEARNER, "week")
        body = app.store.read(ref(saved["review_ref"]), LEARNER).body
        assert body["evidence_refs"] == [final.ref.model_dump()]

    asyncio.run(scenario())


def test_student_capability_cannot_steer_the_teaching_flow(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        parent = {"Authorization": f"Bearer {app.parent_token}"}
        prepared = client.post(
            "/api/parent/prepare", headers=parent, json={"request_id": "p", "learner_id": LEARNER}
        ).json()
        started = client.post(
            "/api/parent/start",
            headers=parent,
            json={
                "request_id": "s",
                "learner_id": LEARNER,
                "course_ref": prepared["course_ref"],
                "conditions": CONDITIONS,
            },
        ).json()
        student = {"Authorization": f"Bearer {started['student_token']}"}
        for command in ("skip", "close", "end_unconfirmed", "resume"):
            response = client.post(
                "/api/student/control",
                headers=student,
                json={"event_id": f"student-{command}", "command": command},
            )
            assert response.status_code == 422
        paused = client.post(
            "/api/student/control", headers=student, json={"event_id": "p1", "command": "pause"}
        )
        assert paused.json()["mode"] == "paused"
        activity = paused.json()["activity_id"]
        resumed = client.post(
            "/api/parent/control",
            headers=parent,
            json={"event_id": "r1", "activity_id": activity, "command": "resume"},
        )
        assert resumed.status_code == 200 and resumed.json()["mode"] == "delivering"
        client.post(
            "/api/student/control", headers=student, json={"event_id": "s1", "command": "stop"}
        )


def test_failed_save_records_next_step_and_deadline_then_resumes(application):
    app = application
    original = app.save_step

    def fail_package(*args, **kwargs):
        if args[2] == "package":
            app.store.fail_at = "before_commit"
        return original(*args, **kwargs)

    app.save_step = fail_package
    with pytest.raises(OSError):
        app.prepare("prepare-failure", LEARNER)
    app.save_step = original
    operation = app.request(
        "prepare-failure",
        LEARNER,
        "prepare",
        {"demand_ref": app.current(LEARNER, "demand").ref.model_dump()},
        sources=(app.current(LEARNER, "demand").ref,),
        baselines=tuple(
            app.current(LEARNER, label).ref
            for label in ("demand", "state", "plan:week", "household")
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
    app.prepare("prepare-failure", LEARNER)
    steps = {row["step_id"]: row["payload"] for row in app.ledger.steps(operation, LEARNER)}
    assert steps["package"]["execution"] == "complete"


def test_lost_save_acknowledgement_is_queried_before_reporting_failure(application):
    app = application
    app.store.fail_at = "after_commit"
    prepared = app.prepare("lost-ack", LEARNER)
    assert app.store.read(ref(prepared["course_ref"]), LEARNER).kind == "course"


def test_seal_progress_database_error_keeps_the_durable_fact(application, monkeypatch):
    app = application
    core = start(app)
    core.control("stop", "stop")

    def locked(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("database is locked"))

    monkeypatch.setattr(app.ledger, "accept", locked)
    fact = app.seal()
    assert app.activity_facts(core.activity_id, LEARNER).ref == fact.ref


def test_successful_seal_retry_clears_the_reported_error(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        parent = {"Authorization": f"Bearer {app.parent_token}"}
        prepared = client.post(
            "/api/parent/prepare", headers=parent, json={"request_id": "p", "learner_id": LEARNER}
        ).json()
        started = client.post(
            "/api/parent/start",
            headers=parent,
            json={
                "request_id": "s",
                "learner_id": LEARNER,
                "course_ref": prepared["course_ref"],
                "conditions": CONDITIONS,
            },
        ).json()
        app.store.fail_at = "before_commit"
        client.post(
            "/api/student/control",
            headers={"Authorization": f"Bearer {started['student_token']}"},
            json={"event_id": "stop", "command": "stop"},
        )
        assert client.get("/api/parent/dashboard", headers=parent).json()["seal_errors"]
        assert client.post("/api/parent/retry-seal", headers=parent).status_code == 200
        dashboard = client.get("/api/parent/dashboard", headers=parent).json()
        assert dashboard["seal_errors"] == {} and dashboard["pending_seals"] == []


def test_inapplicable_plan_proposal_is_rejected_before_any_decision(application):
    app = application
    with pytest.raises(ValueError, match="30 minutes"):
        review(app, "too-long", LEARNER, "week", {"budget_minutes": 45})
    assert not app.store.list_current(LEARNER, "decision")


def test_replayed_start_of_an_ended_activity_never_returns_its_old_result(application):
    app = application
    course = ref(app.prepare("prepare", LEARNER)["course_ref"])
    app.start("start-A", LEARNER, course, CONDITIONS)
    app.core.control(str(uuid4()), "stop")
    app.seal()
    app.start("start-B", LEARNER, course, CONDITIONS)
    with pytest.raises(Conflict, match="original start ended"):
        app.start("start-A", LEARNER, course, CONDITIONS)


async def correct_first_lesson_after_a_second(app):
    """C1 with help, C2 taught and followed by C3, then a correction to C1's first task."""
    first, _, result = await first_lesson_with_next_course(app, helped=True)
    second = await teach(
        app, "start2", result["next_package"]["course_ref"], ("the small bus", "it fell")
    )
    activity = second.body["activity_id"]
    app.feedback("feedback2", LEARNER, activity, "无额外补充")
    third = await app.next_course("next2", LEARNER, activity)
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
        result = await app.next_course("recheck1", LEARNER, first.activity_id)
        state = app.store.read(ref(result["adoption"]["state_ref"]), LEARNER)
        dims = {item["dimension_id"]: item for item in state.body["dimensions"]}
        later = app.store.current(identity(second.body["activity_id"], "evaluation"), LEARNER)
        reading = dims["english.reading.facts"]
        assert reading["evidence_refs"] == [later.ref.model_dump()]
        assert any("较早活动" in item for item in reading["limitations"])
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
