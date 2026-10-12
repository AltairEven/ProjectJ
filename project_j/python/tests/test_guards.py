from dataclasses import replace

import pytest

from project_j.guards import (
    CoreToken,
    accepts_stop,
    evaluation_route,
    input_relation,
    may_deliver,
    recovery_action,
    retry_delay_ms,
)

TOKEN = CoreToken("A1", "Q1", "W1", 3, 0, "running")


@pytest.mark.parametrize(
    "change",
    [
        {"state": "ended", "cancellation_epoch": 1},
        {"state": "paused"},
        {"state": "offscreen"},
        {"task_instance_id": "Q2"},
        {"wait_id": "W2"},
        {"revision": 4},
        {"activity_id": "A2"},
    ],
)
def test_old_results_or_disallowed_state_cannot_be_delivered(change):
    assert not may_deliver(replace(TOKEN, **change), TOKEN, harness_allowed=True)


def test_unapproved_model_fragment_never_becomes_delivery():
    assert not may_deliver(TOKEN, TOKEN, harness_allowed=False)
    assert may_deliver(TOKEN, TOKEN, harness_allowed=True)


def test_stop_ignores_old_task_and_remains_idempotent_after_end():
    current = replace(TOKEN, task_instance_id="Q2", revision=9, state="ended")
    assert accepts_stop(current, "A1", authorized=True)
    assert not accepts_stop(current, "A2", authorized=True)
    assert not accepts_stop(current, "A1", authorized=False)


@pytest.mark.parametrize(
    "delivered,expected",
    [
        ("none", "supplement"),
        ("partial", "new_attempt"),
        ("complete", "new_attempt"),
        ("unknown", "clarify"),
    ],
)
def test_delivered_help_cannot_be_washed_back_into_first_answer(delivered, expected):
    assert (
        input_relation(
            recognition_revision_verified=False,
            original_task=True,
            delivered=delivered,
            says_unfinished=True,
            waiting_retry=True,
        )
        == expected
    )


def test_verified_asr_revision_is_not_a_new_attempt_even_after_delivery():
    assert (
        input_relation(
            recognition_revision_verified=True,
            original_task=True,
            delivered="complete",
            says_unfinished=False,
            waiting_retry=True,
        )
        == "recognition_revision"
    )


def route(**changes):
    return evaluation_route(
        **dict(
            sealed=True,
            feedback_submitted=False,
            later_explicit=False,
            needs_evaluation=False,
            reusable=False,
            inflight_covers=False,
        )
        | changes
    )


def test_seal_refresh_or_later_choice_alone_never_requests_evaluation():
    assert route() == "wait_feedback"
    assert route(later_explicit=True) == "wait_feedback"
    assert route(sealed=False, needs_evaluation=True) == "wait_seal"


def test_normal_feedback_once_provisional_only_when_needed_and_result_reused():
    assert route(feedback_submitted=True) == "final"
    assert route(later_explicit=True, needs_evaluation=True) == "provisional"
    assert route(later_explicit=True, needs_evaluation=True, reusable=True) == "reuse"
    assert route(feedback_submitted=True, reusable=True) == "reuse"
    assert route(later_explicit=True, needs_evaluation=True, inflight_covers=True) == "join"
    assert route(feedback_submitted=True, later_explicit=True, needs_evaluation=True) == "final"


def test_unknown_receipt_must_query_original_not_create_again():
    assert (
        recovery_action(save="unknown", lookup="unknown", generated_available=True)
        == "query_original"
    )
    assert recovery_action(save="unknown", lookup="found", generated_available=False) == "reuse"
    assert recovery_action(save="failed", lookup="absent", generated_available=True) == "retry_save"
    assert (
        recovery_action(save="failed", lookup="absent", generated_available=False) == "report_loss"
    )


def test_retry_limit_and_original_deadline_survive_retries():
    assert retry_delay_ms(failure="429", attempt=1, now_ms=100, deadline_ms=10000) == 250
    assert retry_delay_ms(failure="5xx", attempt=2, now_ms=100, deadline_ms=10000) == 750
    assert retry_delay_ms(failure="5xx", attempt=3, now_ms=100, deadline_ms=10000) is None
    assert retry_delay_ms(failure="429", attempt=1, now_ms=9800, deadline_ms=10000) is None
    assert (
        retry_delay_ms(failure="429", attempt=1, now_ms=0, deadline_ms=10000, retry_after_ms=11000)
        is None
    )
    assert retry_delay_ms(failure="refusal", attempt=1, now_ms=0, deadline_ms=10000) is None
