import asyncio

import pytest
from support import CONDITIONS, LEARNER, answer, complete_story, deliver_all, start

from project_j.application.coordinator import Coordinator
from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody
from project_j.platform.storage import Conflict, SimulationStore, StorageBlocked
from project_j.preparation.contracts import demand_requirements


def test_E2E01_complete_simulation_reaches_actual_C2_and_survives_reopen(application):
    async def scenario():
        core, fact = await complete_story(application)
        assert not any(call["purpose"] == "evaluation" for call in application.model.calls)
        feedback = application.feedback("feedback1", LEARNER, core.activity_id, "无额外补充")
        result = await application.next_course("next1", LEARNER, core.activity_id)
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
            len([call for call in application.model.calls if call["purpose"] == "evaluation"]) == 1
        )
        application.dashboard(LEARNER)
        assert (
            len([call for call in application.model.calls if call["purpose"] == "evaluation"]) == 1
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


def test_E2E03_stop_during_model_does_not_wait_or_deliver_late_result(application):
    async def scenario():
        core = start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        application.model.delay_seconds = 5
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
    core = start(application)
    core.control("stop", "stop")
    application.store.fail_at = failure
    with pytest.raises(OSError):
        application.seal()
    fact = application.seal()
    assert application.activity_facts(core.activity_id, LEARNER).ref == fact.ref
    assert application.seal().ref == fact.ref
    assert not application.model.calls


def test_E2E04_progress_failure_cannot_undo_successful_fact(application):
    core = start(application)
    core.control("stop", "stop")
    application.store.fail_at = "ledger"
    fact = application.seal()
    assert application.activity_facts(core.activity_id, LEARNER).ref == fact.ref
    assert not application.model.calls


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
            application.adopt("adopt", LEARNER, evaluation)
        state = application.current(LEARNER, "state")
        assert state.revision == 2
        assert application.current(LEARNER, "demand").revision == 1
        monkeypatch.setattr(application, "save_step", original)
        result = application.adopt("adopt", LEARNER, evaluation)
        assert application.current(LEARNER, "state").ref == state.ref
        assert result["demand_ref"]["revision"] == 2

    asyncio.run(scenario())


def test_E2E06_feedback_correction_keeps_original_facts_and_changes_sources(application):
    async def scenario():
        core, fact = await complete_story(application)
        application.feedback("feedback1", LEARNER, core.activity_id, "无额外补充")
        first = await application.next_course("next1", LEARNER, core.activity_id)
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
        second = await application.next_course("next2", LEARNER, core.activity_id)
        assert application.store.read(fact.ref, LEARNER).body == fact.body
        assert len(application.store.list_current(LEARNER, "fact")) == 1
        assert application.current(LEARNER, "state").revision > state.revision
        assert first["adoption"]["demand_ref"] != second["adoption"]["demand_ref"]
        assert application.store.list_current(LEARNER, "restriction")

    asyncio.run(scenario())


def test_E2E07_old_course_demand_does_not_start_after_next_demand(application):
    async def scenario():
        old = application.prepare("prepare1", LEARNER)
        core, _ = await complete_story(application)
        application.feedback("feedback", LEARNER, core.activity_id, "无额外补充")
        await application.next_course("next", LEARNER, core.activity_id)
        with pytest.raises(Conflict, match="old demand"):
            application.start(
                "new-start", LEARNER, RevisionRef.model_validate(old["course_ref"]), CONDITIONS
            )

    asyncio.run(scenario())


def test_E2E09_later_choice_and_dashboard_never_call_evaluation(application):
    async def scenario():
        core, _ = await complete_story(application)
        application.later_feedback("later", LEARNER, core.activity_id)
        application.dashboard(LEARNER)
        assert not any(call["purpose"] == "evaluation" for call in application.model.calls)
        with pytest.raises(ValueError):
            await application.evaluate("refresh", LEARNER, core.activity_id, "refresh")
        provisional = await application.evaluate("needed", LEARNER, core.activity_id, "next_course")
        reused = await application.evaluate(
            "needed-again", LEARNER, core.activity_id, "workbench_evaluation"
        )
        assert provisional.ref == reused.ref
        assert (
            len([call for call in application.model.calls if call["purpose"] == "evaluation"]) == 1
        )

    asyncio.run(scenario())


def test_E2E13_fragments_windows_resumption_echo_and_recognition_revision(application):
    async def scenario():
        core = start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        adapter = application.adapter
        await adapter.voice_event("start", "speech1")
        await adapter.voice_event("fragment", "speech1", "a")
        await adapter.voice_event("candidate_end", "speech1", candidate_end_ms=100)
        await adapter.voice_event("final", "speech1", "a car")
        assert not core.attempts
        await adapter.voice_event("resume", "speech1")
        assert not core.attempts
        await adapter.voice_event("candidate_end", "speech1", candidate_end_ms=600)
        await adapter.voice_event("final", "speech1", "a bridge")
        assert not core.attempts
        await adapter.voice_event("window_elapsed", "speech1")
        assert len(core.attempts) == 1
        deliver_all(core)
        await adapter.voice_event("revision", "speech1", "给车搭桥")
        assert len(core.attempts) == 1
        assert core.attempts[0]["recognition_revisions"]
        assert core.attempts[0]["inputs"][0]["text"] == "a bridge"
        await adapter.voice_event("start", "echo1", source="teacher_echo")
        await adapter.voice_event("final", "echo1", "She folded it.", source="teacher_echo")
        assert len(core.attempts) == 1

    asyncio.run(scenario())


def test_supplement_before_delivery_keeps_one_attempt_and_invalidates_old_action(application):
    async def scenario():
        core = start(application)
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


def test_two_same_answers_are_two_attempts_but_transport_replay_is_one(application):
    async def scenario():
        core = start(application)
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
    core = start(application)
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
    prepared = application.prepare("prepare", LEARNER)
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
        application.later_feedback("later", LEARNER, core.activity_id)
        application.model.delay_seconds = 0.03
        old_job = asyncio.create_task(
            application.evaluate("needed", LEARNER, core.activity_id, "next_course")
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
    package = application.prepare("prepare", LEARNER)
    application.initialize("fictional-L2")
    with pytest.raises(ValueError, match="cross-learner"):
        application.store.read(RevisionRef.model_validate(package["course_ref"]), "fictional-L2")
    assert (
        application.current(LEARNER, "state").object_id
        != application.current("fictional-L2", "state").object_id
    )


def test_persisted_start_does_not_reopen_an_activity_after_memory_loss(application):
    app = application
    package = app.prepare("prepare1", LEARNER)
    course = RevisionRef.model_validate(package["course_ref"])
    app.start("start1", LEARNER, course, CONDITIONS)
    app.store.active_activity_id = (
        None  # Explicitly simulate loss, not a recovery of classroom facts.
    )
    reopened = Coordinator(app.store, app.root, verification=app.verification)
    result = reopened.start("start1", LEARNER, course, CONDITIONS)
    assert result["decision"] == "interrupted_unknown"
    assert reopened.core is None
    assert not app.store.list_current(LEARNER, "fact")


def test_completed_requests_replay_after_later_effective_versions_without_new_calls(application):
    async def scenario():
        prepared = application.prepare("first-prepare", LEARNER)
        core, _ = await complete_story(application)
        application.feedback("first-feedback", LEARNER, core.activity_id, "无额外补充")
        result = await application.next_course("first-next", LEARNER, core.activity_id)
        state = application.current(LEARNER, "state").ref
        demand = application.current(LEARNER, "demand").ref
        calls = len(application.model.calls)
        assert application.prepare("first-prepare", LEARNER) == prepared
        assert await application.next_course("first-next", LEARNER, core.activity_id) == result
        assert application.current(LEARNER, "state").ref == state
        assert application.current(LEARNER, "demand").ref == demand
        assert len(application.model.calls) == calls
        with pytest.raises(Conflict):
            application.feedback("first-prepare", LEARNER, core.activity_id, "无额外补充")

    asyncio.run(scenario())


def test_recovered_successful_retry_preserves_original_attempt_count(application):
    app = application
    app.store.fail_at = "temporary_save"
    prepared = app.prepare("temporary-save", LEARNER)
    assert app.prepare("temporary-save", LEARNER) == prepared
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
                    for label in ("demand", "state", "plan:week", "household")
                ),
            ),
            LEARNER,
        )
    )
