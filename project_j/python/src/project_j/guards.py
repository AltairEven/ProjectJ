"""Pure P0 boundary predicates, not a classroom state machine or coordinator."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CoreToken:
    activity_id: str
    task_instance_id: str
    wait_id: str
    revision: int
    cancellation_epoch: int
    state: Literal["running", "offscreen", "paused", "closing", "ended"]


def may_deliver(current: CoreToken, issued: CoreToken, *, harness_allowed: bool) -> bool:
    return harness_allowed and current.state in {"running", "closing"} and current == issued


def accepts_stop(current: CoreToken, activity_id: str, *, authorized: bool) -> bool:
    # Does not require the old task, wait, revision or a model response.
    return authorized and current.activity_id == activity_id


def input_relation(
    *,
    recognition_revision_verified: bool,
    original_task: bool,
    delivered: Literal["none", "partial", "complete", "unknown"],
    says_unfinished: bool,
    waiting_retry: bool,
) -> str:
    if recognition_revision_verified:
        return "recognition_revision"  # Not a new attempt; delivered help is retained.
    if not original_task or delivered == "unknown":
        return "clarify"
    if delivered == "none" and says_unfinished:
        return "supplement"
    if delivered in {"partial", "complete"} and waiting_retry:
        return "new_attempt"
    return "current_wait"


def evaluation_route(
    *,
    sealed: bool,
    feedback_submitted: bool,
    later_explicit: bool,
    needs_evaluation: bool,
    reusable: bool,
    inflight_covers: bool,
) -> str:
    if not sealed:
        return "wait_seal"
    if reusable:
        return "reuse"
    if inflight_covers:
        return "join"
    if feedback_submitted:
        return "final"
    if later_explicit and needs_evaluation:
        return "provisional"
    return "wait_feedback"


def recovery_action(
    *,
    save: Literal["succeeded", "failed", "unknown"],
    lookup: Literal["found", "absent", "unknown"],
    generated_available: bool,
) -> str:
    if save == "succeeded" or lookup == "found":
        return "reuse"
    if lookup == "unknown":
        return "query_original"
    return "retry_save" if generated_available else "report_loss"


def retry_delay_ms(
    *,
    failure: str,
    attempt: int,
    now_ms: float,
    deadline_ms: float,
    retry_after_ms: float = 0,
    jitter_fraction: float = 0,
) -> float | None:
    if failure not in {"connection", "429", "5xx", "save_not_committed"}:
        return None
    if attempt not in {1, 2} or not 0 <= jitter_fraction <= 0.2:
        return None
    delay = max((250 if attempt == 1 else 750) * (1 + jitter_fraction), retry_after_ms)
    return delay if now_ms + delay < deadline_ms else None
