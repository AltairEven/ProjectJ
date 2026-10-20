import asyncio
import json
from uuid import uuid4

import pytest
from support import (
    CONDITIONS,
    LEARNER,
    answer,
    complete_story,
    confirmed,
    deliver_all,
    judgments,
    next_course,
    read,
    read_aloud_all,
    start,
)

from project_j.application.coordinator import Coordinator
from project_j.classroom.harness import PLEA
from project_j.contracts import RevisionRef
from project_j.evaluation.assessment import interpret
from project_j.learning.contracts import DemandBody
from project_j.platform.storage import Conflict, SimulationStore, StorageBlocked
from project_j.preparation.contracts import demand_requirements


def test_E2E01_complete_simulation_reaches_actual_C2_and_survives_reopen(application):
    async def scenario():
        core, fact = await complete_story(application)
        assert not any(call["purpose"] == "evaluation" for call in application.models.calls)
        feedback = application.feedback("feedback1", LEARNER, core.activity_id, "无额外补充")
        result = await next_course(application, "next1", LEARNER, core.activity_id)
        assert feedback["evaluation"] == "not_requested"
        c2 = application.store.read(
            RevisionRef.model_validate(result["next_package"]["course_ref"]), LEARNER
        )
        d2 = application.store.read(
            RevisionRef.model_validate(result["adoption"]["demand_ref"]), LEARNER
        )
        assert c2.kind == "course" and c2.body["demand_ref"] == d2.ref.model_dump()
        mapped = {(item["field"], item["requirement"]) for item in c2.body["demand_mapping"]}
        assert mapped == set(demand_requirements(DemandBody.model_validate(d2.body)))
        assert c2.body["tasks"][0]["prompt"] == "Which bus did Ben try first?"
        assert (
            len([call for call in application.models.calls if call["purpose"] == "evaluation"]) == 1
        )
        application.dashboard(LEARNER)
        assert (
            len([call for call in application.models.calls if call["purpose"] == "evaluation"]) == 1
        )
        reopened = SimulationStore(application.store.root, application.store.run.name)
        assert reopened.read(fact.ref, LEARNER).body == fact.body
        assert reopened.read(c2.ref, LEARNER).body == c2.body
        assert reopened.current(d2.object_id, LEARNER).ref == d2.ref
        reopened.close()

    asyncio.run(scenario())


def test_E2E02_wrong_first_answer_two_different_help_and_offscreen_unknown(application):
    async def scenario():
        core, fact = await complete_story(application, helped=True, offscreen_report=False)
        reading = [
            attempt for attempt in fact.body["attempts"] if attempt["task_id"] == "reading-fact"
        ]
        assert reading[0]["inputs"][0]["text"] == "a car"
        assert reading[-1]["inputs"][0]["text"] == "a bridge"
        assert reading[0]["attempt_id"] != reading[-1]["attempt_id"]
        assert reading[-1]["help"]["english.reading.facts"] == "scaffold"
        help_texts = [
            event["payload"]["text"]
            for event in fact.body["events"]
            if event["kind"] == "allowed_action" and event["payload"]["kind"] == "help"
        ]
        assert len(set(help_texts)) == 3
        assert not any(
            attempt["task_id"] == "paper-experiment" for attempt in fact.body["attempts"]
        )
        application.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        evaluation = await application.evaluate(
            "evaluate", LEARNER, core.activity_id, "final_feedback"
        )
        assert all(
            task["working_judgment"] != "single_independent" for task in evaluation.body["tasks"]
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("helped", [False, True])
def test_E2E02_state_records_english_expression_only_where_help_marked_it(application, helped):
    async def scenario():
        core, _ = await complete_story(application, helped=helped)
        application.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        result = await next_course(application, "next", LEARNER, core.activity_id)
        state = application.store.read(
            RevisionRef.model_validate(result["adoption"]["state_ref"]), LEARNER
        )
        dims = {item["dimension_id"]: item for item in state.body["dimensions"]}
        speaking = dims["english.speaking.report"]
        if helped:
            assert (speaking["judgment"], speaking["help"]) == ("needs_help", "scaffold")
            assert speaking["evidence_refs"] == dims["english.reading.facts"]["evidence_refs"]
        else:
            assert (speaking["judgment"], speaking["evidence_refs"]) == ("unknown", [])

    asyncio.run(scenario())


def test_E2E03_stop_during_model_does_not_wait_or_deliver_late_result(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        application.models.adapters["simulated"].delay_seconds = 5
        work = asyncio.create_task(application.adapter.text("answer1", "a bridge"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert core.mode == "judging"
        core.control("stop1", "stop")
        assert core.mode == "ended" and core.pending is None
        result = await work
        assert result["ack"] == "cancelled"
        fact = application.seal()
        assert fact.body["end"]["reason"] == "stop"
        assert not any(event["kind"] == "approved_proposal" for event in fact.body["events"])
        with pytest.raises(ValueError):
            core.control("resume-after-stop", "resume")

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["before_commit", "after_commit"])
def test_E2E04_seal_failure_or_lost_receipt_retries_same_fact_without_model(application, failure):
    core = asyncio.run(start(application))
    core.control("stop", "stop")
    application.store.fail_at = failure
    with pytest.raises(OSError):
        application.seal()
    fact = application.seal()
    assert application.activity_facts(core.activity_id, LEARNER).ref == fact.ref
    assert application.seal().ref == fact.ref
    assert not judgments(application)


def test_parent_skip_works_while_the_model_is_slow_and_while_offscreen(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        first = core.task_instance_id
        application.models.adapters["simulated"].delay_seconds = 5
        work = asyncio.create_task(application.adapter.text("slow-answer", "a bridge"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert core.mode == "judging"
        core.control("skip-slow", "skip")
        assert core.task_instance_id != first and core.mode == "delivering"
        assert (await work)["ack"] == "cancelled"
        application.models.adapters["simulated"].delay_seconds = 0
        deliver_all(core)
        await answer(application, "She folded it.")
        await answer(application, "我指的是折过的纸。")
        assert core.mode == "offscreen"
        # The offscreen action names its own budget and the way back.
        assert "约7分钟" in core.snapshot()["presented_action"]["text"]
        core.control("skip-offscreen", "skip")
        deliver_all(core)
        await read_aloud_all(application)
        assert core.wait_kind == "closing_confirmation"
        approved = [e["payload"] for e in core.events if e["kind"] == "approved_proposal"]
        assert all(item["event_id"] != "slow-answer" for item in approved)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("text", "plea"),
    [
        ("还是不懂，帮我一下", True),
        ("我需要帮助", True),
        ("能帮我吗？", True),
        ("I need help.", True),
        ("Can you help me?", True),
        ("妈妈帮我扶了书。", False),
        ("不知道", False),
        ("第二次纸折了，车没掉。", False),
    ],
)
def test_report_tasks_treat_only_a_bare_plea_as_help(text, plea):
    assert (not PLEA.sub("", text.casefold())) is plea


def test_a_typed_report_mentioning_help_is_a_report_and_a_bare_plea_is_help(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "听到了。", "confirmation")
        await answer(application, "a bridge")
        await answer(application, "She folded it.")
        assert core.course.tasks[core.task_index].task_id == "point-paper"
        await answer(application, "还是不懂，帮我一下")
        assert core.help_count[core.task_instance_id] == 1
        await answer(application, "妈妈帮我指了那张折过的纸。")
        assert core.course.tasks[core.task_index].task_id == "paper-experiment"
        core.control("return1", "return")
        deliver_all(core)
        await answer(application, "第二次纸折了，车没掉，妈妈帮我扶了书。")
        await read_aloud_all(application)
        assert core.wait_kind == "closing_confirmation"
        reports = [a["judgment"] for a in core.attempts if a["purpose"] in {"point", "offscreen"}]
        assert reports == ["reported", "reported"]

    asyncio.run(scenario())


def test_step_scoped_debug_fault_skips_other_saves_and_fails_only_the_seal_once(application):
    application.store.fail_at = "fact:before_commit"
    core = asyncio.run(start(application))
    core.control("stop", "stop")
    with pytest.raises(OSError):
        application.seal()
    assert application.seal().body["end"]["reason"] == "stop"


def test_E2E04_progress_failure_cannot_undo_successful_fact(application):
    core = asyncio.run(start(application))
    core.control("stop", "stop")
    application.store.fail_at = "ledger"
    fact = application.seal()
    assert application.activity_facts(core.activity_id, LEARNER).ref == fact.ref
    assert not judgments(application)


def test_E2E05_state_saved_demand_failure_resumes_without_rewriting_state(application, monkeypatch):
    async def scenario():
        core, _ = await complete_story(application)
        application.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        evaluation = await application.evaluate(
            "evaluate", LEARNER, core.activity_id, "final_feedback"
        )
        original = application.save_step

        def fail_demand(*args, **kwargs):
            if args[2] == "demand":
                application.store.fail_at = "before_commit"
            return original(*args, **kwargs)

        monkeypatch.setattr(application, "save_step", fail_demand)
        with pytest.raises(OSError):
            await application.adopt("adopt", LEARNER, evaluation)
        state = application.current(LEARNER, "state")
        assert state.revision == 2
        assert application.current(LEARNER, "demand").revision == 1
        monkeypatch.setattr(application, "save_step", original)
        result = await application.adopt("adopt", LEARNER, evaluation)
        assert application.current(LEARNER, "state").ref == state.ref
        assert result["demand_ref"]["revision"] == 2

    asyncio.run(scenario())


def test_E2E06_feedback_correction_keeps_original_facts_and_changes_sources(application):
    async def scenario():
        core, fact = await complete_story(application)
        application.feedback("feedback1", LEARNER, core.activity_id, "无额外补充")
        first = await next_course(application, "next1", LEARNER, core.activity_id)
        state = application.current(LEARNER, "state")
        application.feedback(
            "correction1",
            LEARNER,
            core.activity_id,
            "模拟家长补充：R1曾经代答。",
            corrections=(
                {"task_id": "reading-fact", "note": "家长代答，不能按儿童独立样本解释。"},
            ),
        )
        second = await next_course(application, "next2", LEARNER, core.activity_id)
        assert application.store.read(fact.ref, LEARNER).body == fact.body
        assert len(application.store.list_current(LEARNER, "fact")) == 1
        assert application.current(LEARNER, "state").revision > state.revision
        assert first["adoption"]["demand_ref"] != second["adoption"]["demand_ref"]
        assert application.store.list_current(LEARNER, "restriction")

    asyncio.run(scenario())


def test_E2E07_old_course_demand_does_not_start_after_next_demand(application):
    async def scenario():
        old = await application.prepare("prepare1", LEARNER)
        core, _ = await complete_story(application)
        application.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        await next_course(application, "next", LEARNER, core.activity_id)
        with pytest.raises(Conflict, match="old demand"):
            application.start(
                "new-start", LEARNER, RevisionRef.model_validate(old["course_ref"]), CONDITIONS
            )

    asyncio.run(scenario())


def test_E2E09_refresh_never_evaluates_and_after_class_reuses(application):
    async def scenario():
        core, _ = await complete_story(application)
        application.dashboard(LEARNER)  # Outside the service loop nothing starts by itself.
        assert not any(call["purpose"] == "evaluation" for call in application.models.calls)
        with pytest.raises(ValueError):
            await application.evaluate("refresh", LEARNER, core.activity_id, "refresh")
        provisional = await application.evaluate("needed", LEARNER, core.activity_id, "after_class")
        reused = await application.evaluate(
            "needed-again", LEARNER, core.activity_id, "after_class"
        )
        assert provisional.ref == reused.ref
        assert (
            len([call for call in application.models.calls if call["purpose"] == "evaluation"]) == 1
        )

    asyncio.run(scenario())


def test_supplement_before_delivery_keeps_one_attempt_and_invalidates_old_action(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        first = await application.adapter.text("first", "that thing")
        old_action = core.pending
        await application.adapter.text(
            "supplement", "a bridge", relation="supplement", related="first"
        )
        assert len(core.attempts) == 1 and len(core.attempts[0]["inputs"]) == 2
        assert core.pending != old_action
        deliver_all(core)
        assert first["snapshot"]["pending_action"]["action_id"] not in core.delivered

    asyncio.run(scenario())


def test_student_view_shows_the_story_without_adult_material_metadata(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        [text] = application.student_view(core)["materials"].values()
        assert text.startswith("The Paper Bridge\n\nMia has a toy car.\n")
        assert "素材版本" not in text and "Lexile" not in text

    asyncio.run(scenario())


def test_reopened_student_page_can_show_the_last_presented_action(application):
    async def scenario():
        core = await start(application)
        assert core.snapshot()["presented_action"] is None
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        # The prompt was already delivered to the old page; a reopened page has no pending action.
        snapshot = core.snapshot()
        assert snapshot["pending_action"] is None
        assert snapshot["presented_action"]["text"] == "What does Mia want to make?"

    asyncio.run(scenario())


def test_supplement_after_delivered_response_is_ruled_a_new_attempt(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        await application.adapter.text("first", "that thing")
        deliver_all(core)
        # The clarification was delivered, so the child's "supplement" cannot join the first answer.
        late = await application.adapter.text(
            "late", "a bridge", relation="supplement", related="first"
        )
        assert late["ack"] == "accepted"
        assert [len(attempt["inputs"]) for attempt in core.attempts] == [1, 1]
        assert core.attempts[-1]["judgment"] == "correct"
        ruled = [event["payload"] for event in core.events if event["kind"] == "relation_ruled"]
        assert ruled == [{"event_id": "late", "relation": "new", "reason": "原回应已交付"}]
        replay = await application.adapter.text(
            "late", "a bridge", relation="supplement", related="first"
        )
        assert replay["ack"] == "replayed"

    asyncio.run(scenario())


def test_two_same_answers_are_two_attempts_but_transport_replay_is_one(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        await application.adapter.text("answer1", "a car")
        assert (await application.adapter.text("answer1", "a car"))["ack"] == "replayed"
        assert len(core.attempts) == 1
        deliver_all(core)
        await application.adapter.text("answer2", "a car")
        assert len(core.attempts) == 2

    asyncio.run(scenario())


def test_pause_keeps_memory_and_prevents_seal_or_database_access(application):
    core = asyncio.run(start(application))
    core.control("pause", "pause")
    assert core.mode == "paused"
    with pytest.raises(ValueError):
        application.seal()
    with pytest.raises(StorageBlocked):
        application.dashboard(LEARNER)
    core.control("resume", "resume")
    assert core.mode == "delivering"
    core.control("stop", "stop")
    assert application.seal().body["end"]["reason"] == "stop"


def test_E2E14_missing_failed_and_unverified_start_are_distinct(application):
    prepared = asyncio.run(application.prepare("prepare", LEARNER))
    course = RevisionRef.model_validate(prepared["course_ref"])
    result = application.start("missing", LEARNER, course, CONDITIONS | {"materials": "unknown"})
    assert result["decision"] == "waiting"
    result = application.start("failed", LEARNER, course, CONDITIONS | {"materials": "unavailable"})
    assert result["decision"] == "blocked"
    application.verification = {"result": "unknown"}
    assert application.start("unverified", LEARNER, course, CONDITIONS)["decision"] == "waiting"


def test_E2E18_feedback_arriving_during_model_cannot_publish_old_source(application):
    async def scenario():
        core, _ = await complete_story(application)
        application.models.adapters["simulated"].delay_seconds = 0.03
        old_job = asyncio.create_task(
            application.evaluate("needed", LEARNER, core.activity_id, "after_class")
        )
        await asyncio.sleep(0.005)
        application.feedback("feedback", LEARNER, core.activity_id, "模拟：家长提供过提示。")
        evaluation = await old_job
        assert (
            evaluation.source_revision == application.source_revision(core.activity_id, LEARNER)[0]
        )
        assert evaluation.body["purpose"] == "final"
        assert not any(
            doc.source_revision == 1
            for doc in application.store.list_current(LEARNER, "evaluation")
        )

    asyncio.run(scenario())


def test_E2E19_cross_learner_course_and_late_results_cannot_publish(application):
    package = asyncio.run(application.prepare("prepare", LEARNER))
    application.initialize("fictional-L2")
    with pytest.raises(ValueError, match="cross-learner"):
        application.store.read(RevisionRef.model_validate(package["course_ref"]), "fictional-L2")
    assert (
        application.current(LEARNER, "state").object_id
        != application.current("fictional-L2", "state").object_id
    )


def test_persisted_start_does_not_reopen_an_activity_after_memory_loss(application):
    app = application
    package = asyncio.run(app.prepare("prepare1", LEARNER))
    course = RevisionRef.model_validate(package["course_ref"])
    confirmed(app, {"course_ref": course})
    app.start("start1", LEARNER, course, CONDITIONS)
    app.store.active_activity_id = (
        None  # Explicitly simulate loss, not a recovery of classroom facts.
    )
    reopened = Coordinator(app.store, app.root, verification=app.verification)
    result = reopened.start("start1", LEARNER, course, CONDITIONS)
    assert result["decision"] == "interrupted_unknown"
    assert reopened.core is None
    assert not app.store.list_current(LEARNER, "fact")


def test_dashboard_shows_the_gap_of_an_activity_lost_with_process_memory(application):
    app = application
    course = RevisionRef.model_validate(asyncio.run(app.prepare("prepare1", LEARNER))["course_ref"])
    confirmed(app, {"course_ref": course})
    started = app.start("start1", LEARNER, course, CONDITIONS)
    app.store.active_activity_id = None  # Explicitly simulate process loss.
    reopened = Coordinator(app.store, app.root, verification=app.verification)
    [lost] = reopened.dashboard(LEARNER)["lost_activities"]
    assert lost["activity_id"] == started["snapshot"]["activity_id"]
    assert lost["course_ref"] == course.model_dump()
    assert not app.store.list_current(LEARNER, "fact")


def test_parent_report_on_a_lost_activity_is_only_saved_as_a_new_source(application):
    app = application
    course = RevisionRef.model_validate(asyncio.run(app.prepare("prepare1", LEARNER))["course_ref"])
    confirmed(app, {"course_ref": course})
    activity = app.start("start1", LEARNER, course, CONDITIONS)["snapshot"]["activity_id"]
    app.store.active_activity_id = None  # Explicitly simulate process loss.
    reopened = Coordinator(app.store, app.root, verification=app.verification)
    text = "家长补记：读完故事，第一题答了a bridge，之后断电。"
    saved = reopened.lost_report("report1", LEARNER, activity, text)
    assert reopened.lost_report("report1", LEARNER, activity, text) == saved
    report = app.store.read(RevisionRef.model_validate(saved["report_ref"]), LEARNER)
    assert (report.kind, report.occurred_at) == ("parent_report", None)
    assert (report.body["activity_id"], report.body["text"]) == (activity, text)
    [lost] = reopened.dashboard(LEARNER)["lost_activities"]
    assert [item["text"] for item in lost["reports"]] == [text]
    # The gap stays; nothing is replayed into a fact or sent to evaluation.
    assert not reopened.models.calls and not app.store.list_current(LEARNER, "fact")
    with pytest.raises(LookupError):
        reopened.lost_report("report2", LEARNER, "not-a-lost-activity", text)


def test_sealed_activity_is_not_reported_lost(application):
    async def scenario():
        await complete_story(application)
        assert application.dashboard(LEARNER)["lost_activities"] == []

    asyncio.run(scenario())


def test_completed_requests_replay_after_later_effective_versions_without_new_calls(application):
    async def scenario():
        prepared = await application.prepare("first-prepare", LEARNER)
        core, _ = await complete_story(application)
        application.feedback("first-feedback", LEARNER, core.activity_id, "无额外补充")
        result = await next_course(application, "first-next", LEARNER, core.activity_id)
        state = application.current(LEARNER, "state").ref
        demand = application.current(LEARNER, "demand").ref
        calls = len(application.models.calls)
        assert (await application.prepare("first-prepare", LEARNER)) == prepared
        assert await next_course(application, "first-next", LEARNER, core.activity_id) == result
        assert application.current(LEARNER, "state").ref == state
        assert application.current(LEARNER, "demand").ref == demand
        assert len(application.models.calls) == calls
        with pytest.raises(Conflict):
            application.feedback("first-prepare", LEARNER, core.activity_id, "无额外补充")

    asyncio.run(scenario())


def test_recovered_successful_retry_preserves_original_attempt_count(application):
    app = application
    app.store.fail_at = "temporary_save"
    prepared = asyncio.run(app.prepare("temporary-save", LEARNER))
    assert asyncio.run(app.prepare("temporary-save", LEARNER)) == prepared
    assert any(
        row["payload"]["attempts"] == 2
        for row in app.ledger.steps(
            app.request(
                "temporary-save",
                LEARNER,
                "prepare",
                {
                    "demand_ref": app.current(LEARNER, "demand").ref.model_dump(),
                },
                sources=(app.current(LEARNER, "demand").ref,),
                baselines=tuple(
                    app.current(LEARNER, label).ref
                    for label in ("demand", "state", "plan:week", "profile")
                ),
            ),
            LEARNER,
        )
    )


async def reach_reading(app):
    core = await start(app)
    deliver_all(core)
    await answer(app, "收到", "confirmation")
    await answer(app, "a bridge")
    await answer(app, "She folded it.")
    await answer(app, "我指的是折过的纸。", "report")
    core.control(str(uuid4()), "return")
    deliver_all(core)
    await answer(app, "第二次纸折了，车没有掉。", "report")
    assert core.reading_task().task_id == "read-aloud-1"
    return core


def test_E2E13_reading_is_scored_first_and_a_reread_is_a_new_attempt(application):
    async def scenario():
        core = await reach_reading(application)
        task = core.reading_task()
        assert core.snapshot()["recording"] == {
            "max_seconds": 300,
            "reference_text": task.reference_text,
        }
        with pytest.raises(ValueError):
            await application.adapter.text("typed-reading", task.reference_text)
        misread = task.reference_text.replace("bridge", "brige", 1)
        await read(application, "r1", misread)
        first = core.attempts[-1]
        first_score = dict(first["pronunciation"])
        assert first_score["status"] == "scored" and first["judgment"] == "incorrect"
        assert first["inputs"][0]["text"] is None
        call = application.models.calls[-1]
        assert call["purpose"] == "classroom_judgment"
        deliver_all(core)
        await read(application, "r2", task.reference_text)
        rereads = [a for a in core.attempts if a["task_id"] == task.task_id]
        assert len(rereads) == 2 and rereads[-1]["judgment"] == "correct"
        assert rereads[0]["pronunciation"] == first_score  # Earlier score kept unchanged.
        deliver_all(core)
        await read(application, "silent", "")
        assert core.attempts[-1]["pronunciation"]["status"] == "unknown"
        assert core.attempts[-1]["judgment"] == "unclear"
        # The recording itself never enters events or attempts.
        assert "brige" not in json.dumps(core.events, ensure_ascii=False)
        assert "brige" not in json.dumps(core.attempts, ensure_ascii=False)

    asyncio.run(scenario())


def test_a_recording_on_a_question_is_rejected(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        with pytest.raises(ValueError):
            await read(application, "not-reading", "a bridge")
        assert not core.attempts

    asyncio.run(scenario())


def test_help_still_works_on_a_reading_segment(application):
    async def scenario():
        core = await reach_reading(application)
        await answer(application, "帮我一下", "help")
        assert core.help_count[core.task_instance_id] == 1
        assert core.reading_task().task_id == "read-aloud-1"

    asyncio.run(scenario())


def test_over_limit_recording_is_rejected_and_max_duration_is_accepted(application):
    async def scenario():
        core = await reach_reading(application)
        with pytest.raises(ValueError):
            await read(application, "too-long", "Mia", recording_ms=300_001)
        result = await read(
            application, "auto-end", core.reading_task().reference_text, 300_000, "max_duration"
        )
        assert result["ack"] == "accepted"

    asyncio.run(scenario())


def test_a_resent_recording_is_replayed_without_a_second_score(application):
    async def scenario():
        core = await reach_reading(application)
        text = core.reading_task().reference_text
        await read(application, "same", text)
        attempts, calls = len(core.attempts), len(application.models.calls)
        assert (await read(application, "same", text))["ack"] == "replayed"
        assert len(core.attempts) == attempts and len(application.models.calls) == calls

    asyncio.run(scenario())


def test_stop_during_scoring_drops_the_late_score(application):
    async def scenario():
        core = await reach_reading(application)
        gate = asyncio.Event()
        scorer = application.scorer
        original = scorer.score

        async def slow(audio, reference):
            await gate.wait()
            return await original(audio, reference)

        scorer.score = slow
        job = asyncio.create_task(read(application, "slow", core.reading_task().reference_text))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        core.control("stop-scoring", "stop")
        gate.set()
        assert (await job)["ack"] == "cancelled"
        assert core.mode == "ended" and core.attempts[-1]["judgment"] == "unclear"
        assert "pronunciation" not in core.attempts[-1]

    asyncio.run(scenario())


def test_pronunciation_scores_reach_evaluation_rows_by_segment(application):
    async def scenario():
        core, fact = await complete_story(application)
        rows = interpret(fact, [], 1, "final")["tasks"]
        reading = [r for r in rows if r["dimension_id"] == "english.speaking.pronunciation"]
        assert [r["task_id"] for r in reading] == ["read-aloud-1", "read-aloud-2"]
        assert all(r["pronunciation"]["status"] == "scored" for r in reading)
        assert all(r["task_judgment"] == "correct" for r in reading)
        others = [r for r in rows if r["dimension_id"] != "english.speaking.pronunciation"]
        assert all(r["pronunciation"] is None for r in others)

    asyncio.run(scenario())
