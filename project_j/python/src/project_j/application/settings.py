"""Runtime settings the student changes from the settings page (ADR-0025): how long each
kind of model call may wait, and which model each purpose is bound to."""

import logging
import time

from project_j.application.progress import in_class
from project_j.platform.models.contracts import Binding, DeadlineKey, Purpose
from project_j.platform.models.init import probe_all
from project_j.platform.models.registry import load_service
from project_j.platform.storage import Conflict

log = logging.getLogger(__name__)

FIXED = "Settings are fixed in simulation"
IN_CLASS = "A class is running; change the model after it ends."
BUSY = "AI work is running; change the model after it finishes."


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


def save_bindings(app, purposes: dict[Purpose, Binding]) -> dict:
    """The bindings are written; the service refuses every purpose until a probe passes."""
    files = models_idle(app)
    files.configure(files.default(), purposes)
    app.models = load_service(files)
    log.info(
        "settings bindings saved purposes=%s",
        ";".join(f"{p}={b.adapter}:{b.model}:{b.effort}:{b.tier}" for p, b in purposes.items()),
    )
    return app.models.status()


async def probe(app) -> dict:
    """Run the same probe as project-j-init; on success the service takes the new bindings."""
    files = models_idle(app)
    started = time.perf_counter()
    try:
        await probe_all(files)
    except Exception as error:  # Kind only; provider text may carry secrets.
        elapsed = (time.perf_counter() - started) * 1000
        log.warning(
            "settings probe outcome=fail reason_kind=%s elapsed_ms=%.0f",
            type(error).__name__,
            elapsed,
        )
        raise Conflict(
            f"The probe failed ({type(error).__name__}); check the model setup and try again."
        ) from None
    old, app.models = app.models, load_service(files)
    for adapter in old.adapters.values():
        if hasattr(adapter, "aclose"):
            await adapter.aclose()
    status = app.models.status()
    log.info(
        "settings probe outcome=%s elapsed_ms=%.0f",
        "pass" if status["initialized"] else "refused",
        (time.perf_counter() - started) * 1000,
    )
    return status
