"""Runtime settings the student changes from the settings page (ADR-0025): how long each
kind of model call may wait, and which model each purpose is bound to."""

import asyncio
import logging
import time

from project_j.application.progress import in_class
from project_j.platform.models.contracts import PURPOSE_LABELS, Binding, DeadlineKey, Purpose
from project_j.platform.models.init import probe_bindings
from project_j.platform.models.registry import load_service, versions
from project_j.platform.storage import Conflict

log = logging.getLogger(__name__)

FIXED = "Settings are fixed in simulation"
IN_CLASS = "A class is running; change the model after it ends."
BUSY = "AI work is running; change the model after it finishes."
PROBING = "A new setup is being checked; wait for it to finish."
STARTED = "A class or AI work started while checking; save again after it ends."
LIST_SECONDS = 10  # A model list that takes longer is left out; the page then takes typing.


def files_of(app):
    if app.models.files is None:
        raise Conflict(FIXED)
    return app.models.files


def models_idle(app):
    """The bindings change only between classes and while no background AI work runs."""
    files = files_of(app)
    if in_class(app):
        raise Conflict(IN_CLASS)
    if app.jobs:
        raise Conflict(BUSY)
    return files


def save_deadlines(app, values: dict[DeadlineKey, int]) -> dict:
    """Written now, read by the next call of each kind; allowed during a class."""
    files = files_of(app)
    before = files.deadlines()
    files.configure_deadlines(values)
    changed = sorted(key for key, value in values.items() if before.get(key) != value)
    log.info("settings deadlines saved changed=%s", ",".join(changed) or "none")
    return app.models.status()


async def reload(app, files):
    """The service takes the file's setup; the old adapters' runtimes are closed."""
    old, app.models = app.models, load_service(files)
    closing = [a for a in old.adapters.values() if hasattr(a, "aclose")]
    for adapter in closing:
        await adapter.aclose()
    log.info("settings models reloaded closed=%d", len(closing))


async def save_bindings(app, purposes: dict[Purpose, Binding]) -> dict:
    """The candidate setup is probed first; only a passed probe replaces the current one."""
    files = models_idle(app)
    if app.probing:
        raise Conflict(PROBING)
    app.probing, started = True, time.perf_counter()

    def outcome(result, reason_kind="none", switched="no"):
        log.info(
            "settings bindings probe outcome=%s reason_kind=%s switched=%s elapsed_ms=%.0f",
            result,
            reason_kind,
            switched,
            (time.perf_counter() - started) * 1000,
        )

    try:
        try:
            record = await probe_bindings(files, purposes)
        except Exception as error:  # Kind only; provider text may carry secrets.
            outcome("fail", type(error).__name__)
            raise Conflict(
                f"The probe failed ({type(error).__name__}); check the model setup and try again."
            ) from None
        if not record["initialized"]:
            outcome("refused")
            reasons = "; ".join(
                f"{PURPOSE_LABELS[purpose]}: {reason}"
                for purpose, item in record["purposes"].items()
                for reason in item["reasons"]
            )
            raise Conflict(f"The new setup did not pass: {reasons}. The current setup stays.")
        if in_class(app) or app.jobs:  # Started while the probe ran; it keeps the current model.
            outcome("pass", "started")
            raise Conflict(STARTED)
        files.configure(files.default(), purposes)
        files.save_init({"config_digest": files.digest(versions(purposes))} | record)
        await reload(app, files)
        await app.models.prewarm()
        outcome("pass", switched="yes")
    finally:
        app.probing = False
    log.info(
        "settings bindings saved purposes=%s",
        ";".join(f"{p}={b.adapter}:{b.model}:{b.effort}:{b.tier}" for p, b in purposes.items()),
    )
    return app.models.status()


async def list_models(app) -> dict:
    """What each set-up adapter offers now; a list that cannot be read is left out."""
    files_of(app)
    if in_class(app):
        raise Conflict(IN_CLASS)
    offered = {}
    for adapter_id, adapter in app.models.adapters.items():
        started, result = time.perf_counter(), "ok"
        try:
            offered[adapter_id] = await asyncio.wait_for(adapter.listed(), LIST_SECONDS)
        except Exception as error:  # Kind only; provider text may carry secrets.
            result = f"unavailable reason_kind={type(error).__name__}"
        log.info(
            "settings models listed adapter=%s result=%s count=%d elapsed_ms=%.0f",
            adapter_id,
            result,
            len(offered.get(adapter_id, [])),
            (time.perf_counter() - started) * 1000,
        )
    return {"models": offered}
