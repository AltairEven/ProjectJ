import asyncio
from datetime import UTC, datetime

import pytest
from support import LEARNER, answer, complete_story, deliver_all, judgments, start

from project_j.classroom.harness import DeliveryFeedback
from project_j.classroom.judgment import ModelProposal
from project_j.contracts import SubmittedInput
from project_j.platform.storage import Conflict


def test_delivery_partial_interruption_and_sequence_cannot_forge_receipt_or_understanding(
    application,
):
    core = asyncio.run(start(application))
    action = core.actions[core.pending]
    partial = DeliveryFeedback(
        delivery_event_id="dl1",
        delivery_id="delivery",
        action_id=action.action_id,
        activity_id=core.activity_id,
        task_instance_id=action.task_instance_id,
        sequence=1,
        output_device_id="sim-output",
        modality="simulated_audio",
        status="partial",
        delivered_range=("opening_part",),
        occurred_at=datetime.now(UTC),
    )
    core.delivery(partial)
    assert core.mode == "delivering" and core.wait_kind == "goal_confirmation"
    core.delivery(
        partial.model_copy(
            update={"delivery_event_id": "dl2", "sequence": 2, "status": "interrupted"}
        )
    )
    assert core.wait_kind == "delivery_recovery"
    with pytest.raises(Conflict):
        core.delivery(
            partial.model_copy(update={"delivery_event_id": "old-completed", "status": "completed"})
        )
    assert not any(event["kind"] == "goal_received" for event in core.events)


def test_E2E11_out_of_bound_proposal_does_not_advance(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        before = core.task_index
        with pytest.raises(Conflict):
            core.approve(
                ModelProposal(
                    activity_id=core.activity_id,
                    task_instance_id="wrong-task",
                    wait_id=core.wait_id,
                    core_revision=core.revision,
                    cancellation_epoch=core.epoch,
                    event_id="not-received",
                    judgment="correct",
                    evidence="valid-looking JSON is not permission",
                    text="未经允许的内容",
                    call_id="test-call",
                )
            )
        assert core.task_index == before and core.mode == "waiting_input"
        assert core.snapshot()["actual_api"] == "未验证"

    asyncio.run(scenario())


def test_stale_input_after_next_question_is_not_scored(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        old = application.adapter.binding()
        await answer(application, "a bridge")
        event = SubmittedInput(
            **old,
            event_id="old-input",
            client_sequence=99,
            realm="simulation",
            source_type="simulated",
            modality="text",
            provider="simulated_speaker",
            text="a bridge",
            intent="answer",
            completion_trigger="explicit",
        )
        before = len(core.attempts)
        with pytest.raises(Conflict):
            await core.submit(event)
        assert len(core.attempts) == before

    asyncio.run(scenario())


def test_model_failure_keeps_original_input_and_does_not_advance(application):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")
        application.models.adapters["simulated"].fail_next = True
        result = await application.adapter.text("failure-input", "a bridge")
        assert result["ack"] == "failed"
        assert core.task_index == 0 and core.attempts[0]["inputs"][0]["text"] == "a bridge"

    asyncio.run(scenario())


def test_budget_boundaries_are_explicit_simulated_clock_events(application):
    core = asyncio.run(start(application))
    core.started_clock -= 27 * 60 + 1
    core.tick()
    assert core.wait_kind == "closing_confirmation"
    assert any(event["kind"] == "budget_boundary" for event in core.events)
    core.started_clock -= 3 * 60
    core.tick()
    assert core.mode == "ended" and core.end["reason"] == "unconfirmed"
    assert application.seal().body["attempts"] == []


def test_E2E18_explicit_no_addition_reuses_provisional_without_another_model_call(application):
    async def scenario():
        core, _ = await complete_story(application)
        first = await application.evaluate("needed", LEARNER, core.activity_id, "after_class")
        application.feedback("no-addition", LEARNER, core.activity_id, "无额外补充")
        second = await application.evaluate("final", LEARNER, core.activity_id, "final_feedback")
        assert first.ref != second.ref and second.body["purpose"] == "final"
        assert (
            len([call for call in application.models.calls if call["purpose"] == "evaluation"]) == 1
        )

    asyncio.run(scenario())


def test_typed_stop_bypasses_question_binding(application):
    async def scenario():
        core = await start(application)
        result = await application.adapter.text("stop-typed", "停止")
        assert result["ack"] == "accepted_control"
        assert core.mode == "ended" and core.pending is None and not core.attempts
        assert not judgments(application)

    asyncio.run(scenario())


def test_repeat_revisions_prevent_old_delivery_from_advancing(application):
    core = asyncio.run(start(application))
    old = core.actions[core.pending]
    revision = core.revision
    core.control("repeat-goal", "repeat")
    assert core.revision > revision
    core.delivery(
        DeliveryFeedback(
            delivery_event_id="old-delivery",
            delivery_id="old-stream",
            action_id=old.action_id,
            activity_id=core.activity_id,
            task_instance_id=old.task_instance_id,
            sequence=1,
            output_device_id="mock-text",
            modality="text",
            status="completed",
            delivered_range=("text_content",),
            occurred_at=datetime.now(UTC),
        )
    )
    assert core.pending != old.action_id and core.task_index == -1


def test_late_actual_delivery_is_an_append_only_fact_revision(application):
    core = asyncio.run(start(application))
    action = core.actions[core.pending]
    core.control("stop-before-late-delivery", "stop")
    old = application.seal()
    report = DeliveryFeedback(
        delivery_event_id="late-partial",
        delivery_id="late-stream",
        action_id=action.action_id,
        activity_id=core.activity_id,
        task_instance_id=action.task_instance_id,
        sequence=1,
        output_device_id="mock-text",
        modality="text",
        status="partial",
        delivered_range=("opening_part",),
        occurred_at=datetime.now(UTC),
    )
    core.delivery(report)
    application.late_delivery(report)
    application.late_delivery(report)
    current = application.activity_facts(core.activity_id, LEARNER)
    assert current.revision == 2 and current.supersedes == old.ref
    assert application.store.read(old.ref, LEARNER).body == old.body
    assert len(current.body["late_delivery_reports"]) == 1
    assert core.mode == "ended" and core.pending is None


def test_seal_progress_is_healed_from_the_durable_fact_receipt(application):
    from project_j.application.documents import identity

    core = asyncio.run(start(application))
    core.control("stop-with-ledger-failure", "stop")
    application.store.fail_at = "ledger"
    fact = application.seal()
    operation = identity(core.activity_id, "seal-operation")
    assert application.ledger.result(operation, LEARNER) is None
    assert application.seal().ref == fact.ref
    assert application.ledger.result(operation, LEARNER)["fact_ref"] == fact.ref.model_dump()


def test_invalid_structured_proposal_retains_input_and_offers_clarification(
    application, monkeypatch
):
    async def scenario():
        core = await start(application)
        deliver_all(core)
        await answer(application, "收到", "confirmation")

        monkeypatch.setattr(
            application.models.adapters["simulated"],
            "respond",
            lambda request: {
                "judgment": "correct",
                "evidence": "outside fixed content",
                "text": "This should not be delivered.",
            },
        )
        result = await application.adapter.text("invalid-proposal-input", "a bridge")
        assert result["ack"] == "rejected_proposal"
        assert core.task_index == 0 and core.actions[core.pending].kind == "clarification"
        assert core.attempts[0]["inputs"][0]["text"] == "a bridge"
        assert not any(event["kind"] == "approved_proposal" for event in core.events)

    asyncio.run(scenario())
