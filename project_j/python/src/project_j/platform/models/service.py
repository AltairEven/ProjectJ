"""Unified model entry: purpose bindings, admission, output checks and call records."""

import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from project_j.platform.models.config import ModelFiles
from project_j.platform.models.contracts import (
    DEFAULT_DEADLINES,
    PURPOSE_LABELS,
    PURPOSES,
    AdapterReply,
    Binding,
    CapabilityReport,
    DeadlineKey,
    ModelAdapter,
    ModelRequest,
    ModelResult,
    Purpose,
    admission,
)
from project_j.platform.models.simulated import SIMULATED_MODEL, SimulatedAdapter
from project_j.platform.storage import Conflict
from project_j.platform.visuals import image_type

log = logging.getLogger(__name__)


class ModelsNotReady(Conflict):
    """Preparation, start, review and evaluation wait for model initialization."""


class ModelService:
    def __init__(
        self,
        adapters: dict[str, ModelAdapter],
        bindings: dict[Purpose, Binding],
        reports: list[CapabilityReport],
        problem: str | None = None,
        files: ModelFiles | None = None,
    ):
        self.adapters, self.bindings, self.reports = adapters, bindings, reports
        self.problem = problem
        self.files = files  # The configuration read at each call; None for the simulation.
        self.calls: list[dict] = []

    @classmethod
    def simulated(cls) -> ModelService:
        """The simulation entry initializes in memory and never reads real configuration."""
        adapter = SimulatedAdapter()
        medium = Binding(adapter="simulated", model=SIMULATED_MODEL, effort="medium")
        bindings: dict[Purpose, Binding] = {
            "planning": medium,
            "preparation": medium,
            "classroom_judgment": Binding(adapter="simulated", model=SIMULATED_MODEL, effort="low"),
            "evaluation": medium,
            "vocabulary": Binding(adapter="simulated", model=SIMULATED_MODEL, effort="low"),
        }
        report = adapter.report(SIMULATED_MODEL, ("low", "medium"))
        return cls({"simulated": adapter}, bindings, [report])

    async def prewarm(self):
        """Start the adapters' runtimes now, not inside the first in-class judgment."""
        for adapter in self.adapters.values():
            if hasattr(adapter, "prewarm"):
                await adapter.prewarm()

    def deadlines(self) -> dict[DeadlineKey, int]:
        return self.files.deadlines() if self.files else dict(DEFAULT_DEADLINES)

    def deadline(self, key: DeadlineKey) -> float:
        """Seconds a call of this kind may wait, as configured right now (ADR-0025)."""
        return self.deadlines()[key]

    def simulates(self, purpose: Purpose) -> bool:
        """Whether this purpose is bound to a simulated adapter (no binding: not simulated)."""
        binding = self.bindings.get(purpose)
        adapter = self.adapters.get(binding.adapter) if binding else None
        return bool(adapter and adapter.simulated)

    def report_for(self, binding: Binding) -> CapabilityReport | None:
        return next(
            (r for r in self.reports if r.adapter == binding.adapter and r.model == binding.model),
            None,
        )

    def sees_images(self, purpose: Purpose) -> bool:
        """Whether this purpose's model passed the image-input probe (the picture check)."""
        binding = self.bindings.get(purpose)
        report = self.report_for(binding) if binding else None
        item = report.items.get("image_input") if report else None
        return bool(item and item.status == "verified")

    def reasons(self, purpose: Purpose) -> list[str]:
        if self.problem:
            return [self.problem]
        binding = self.bindings.get(purpose)
        if binding is None or binding.adapter not in self.adapters:
            return ["no model set for this use"]
        return admission(self.report_for(binding), purpose, binding.effort, binding.tier)

    def require(self):
        reasons = [
            f"{PURPOSE_LABELS[purpose]}: {reason}"
            for purpose in PURPOSES
            for reason in self.reasons(purpose)
        ]
        if reasons:
            raise ModelsNotReady(
                "The model is not set up: " + "; ".join(reasons) + ". Run project-j-init probe."
            )

    async def run(self, request: ModelRequest) -> ModelResult:
        binding = self.bindings.get(request.purpose)
        adapter = self.adapters.get(binding.adapter) if binding else None
        entry = {
            "call_id": str(uuid4()),
            "purpose": request.purpose,
            "at": datetime.now(UTC).isoformat(),
            "adapter": binding.adapter if binding else None,
            "adapter_version": adapter.version if adapter else None,
            "simulated": adapter.simulated if adapter else None,
            "model": binding.model if binding else None,
            "effort": binding.effort if binding else None,
            "tier": binding.tier if binding else None,
            "failure": None,
            "actual": {},
            "usage": None,
            "request_id": None,
            "elapsed_ms": None,
            "images": len(request.images),
        }
        seconds = (
            request.deadline_seconds
            if request.deadline_seconds is not None
            else self.deadline(request.deadline_key)
        )
        entry["deadline_seconds"] = seconds
        request = replace(request, deadline_seconds=seconds, deadline_key=None)
        self.calls.append(entry)
        started = time.perf_counter()
        reply = AdapterReply(data=None, failure="not_initialized")
        try:
            if adapter and not self.reasons(request.purpose):
                try:
                    reply = await asyncio.wait_for(adapter.invoke(request, binding), seconds)
                except TimeoutError:
                    reply = AdapterReply(data=None, failure="timeout")
                except Exception as error:  # Kind only; provider text may carry secrets.
                    entry["error_type"] = type(error).__name__
                    reply = AdapterReply(data=None, failure="unavailable")
        except asyncio.CancelledError:
            entry["failure"] = "cancelled"
            raise
        finally:
            entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            if entry["failure"] == "cancelled":
                self.log(entry)
        output, failure, media = None, reply.failure, ()
        if failure is None and any(check.status == "mismatch" for check in reply.actual.values()):
            failure = "config_mismatch"
        if failure is None:
            try:
                output = request.output.model_validate(reply.data)
            except ValidationError:
                failure = "invalid_output"
        if failure is None and request.media:
            try:
                if len(reply.media) != 1:
                    raise ValueError("one picture per request")
                image_type(reply.media[0])
                media = reply.media
            except ValueError:
                output, failure = None, "invalid_output"
        entry |= {
            "failure": failure,
            "actual": {name: check.model_dump() for name, check in reply.actual.items()},
            "usage": reply.usage,
            "request_id": reply.request_id,
            "media": len(media),
            "media_bytes": sum(len(item) for item in media),
        }
        self.log(entry)
        return ModelResult(
            entry["call_id"], request.purpose, binding, output, failure, reply.actual, media
        )

    @staticmethod
    def log(entry: dict):
        log.info(
            "model %s adapter=%s model=%s effort=%s tier=%s failure=%s elapsed_ms=%.0f "
            "error_type=%s request_id=%s usage=%s media=%s media_bytes=%s images=%s",
            entry["purpose"],
            entry["adapter"],
            entry["model"],
            entry["effort"],
            entry["tier"],
            entry["failure"],
            entry["elapsed_ms"],
            entry.get("error_type"),
            entry["request_id"],
            entry["usage"],
            entry.get("media", 0),
            entry.get("media_bytes", 0),
            entry.get("images", 0),
        )

    def status(self) -> dict:
        """What the parent page shows; bindings and reports only, never credentials."""
        purposes = {
            purpose: {
                "binding": binding.model_dump()
                if (binding := self.bindings.get(purpose))
                else None,
                "reasons": self.reasons(purpose),
            }
            for purpose in PURPOSES
        }
        used = [
            self.adapters[b.adapter] for b in self.bindings.values() if b.adapter in self.adapters
        ]
        return {
            "initialized": not any(item["reasons"] for item in purposes.values()),
            "simulated": bool(used) and all(adapter.simulated for adapter in used),
            "purposes": purposes,
            "reports": [report.model_dump(mode="json") for report in self.reports],
            "deadlines": self.deadlines(),
            "deadline_defaults": dict(DEFAULT_DEADLINES),
            "configurable": self.files is not None,
        }
