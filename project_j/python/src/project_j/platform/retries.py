"""Bounded retries for an explicitly confirmed uncommitted transient save only."""

import time

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_before_delay,
    wait_chain,
    wait_random,
)


class UncommittedTransientSave(OSError):
    pass


def retry_save(action, deadline: float):
    attempts = 0
    policy = Retrying(
        retry=retry_if_exception_type(UncommittedTransientSave),
        wait=wait_chain(wait_random(0.250, 0.300), wait_random(0.750, 0.900)),
        stop=stop_after_attempt(3) | stop_before_delay(max(0, deadline - time.monotonic())),
        reraise=True,
    )
    for attempt in policy:
        with attempt:
            if time.monotonic() >= deadline:
                raise TimeoutError("original save deadline elapsed")
            attempts += 1
            result = action()
    return result, attempts
