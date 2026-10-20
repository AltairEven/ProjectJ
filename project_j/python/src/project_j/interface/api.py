"""Loopback simulation API with separate parent and per-activity student capabilities."""

import argparse
import asyncio
import base64
import binascii
import json
import logging
import time
import traceback
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field, JsonValue, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from project_j.application.configuration import Settings
from project_j.application.coordinator import Coordinator
from project_j.classroom.harness import AllowedAction, DeliveryFeedback, Harness
from project_j.contracts import Contract, Document, RevisionRef
from project_j.curriculum.corrections import correct, release
from project_j.evaluation.assessment import EvaluationUnavailable
from project_j.learning.catalog import Capability, define, resolve
from project_j.learning.maintenance import decide, review, revise_demand_after_plan
from project_j.learning.planning import PlanningUnavailable
from project_j.platform.files import PrivateFiles
from project_j.platform.models.config import ModelFiles
from project_j.platform.models.registry import load_service
from project_j.platform.speech.local import LocalScorer, ScorerUnavailable
from project_j.platform.storage import Conflict, SimulationStore, StorageBlocked
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.local import LocalSynthesizer, SynthesizerUnavailable
from project_j.platform.tts.recorded import speak
from project_j.preparation.drafting import PreparationUnavailable
from project_j.preparation.resources import adopt_resource, generate_resource, resource_file


class ParentRequest(Contract):
    request_id: str = Field(min_length=1)
    learner_id: str = Field(default="fictional-L1", pattern=r"^fictional-[A-Za-z0-9_-]+$")
    realm: Literal["simulation"] = "simulation"
    source_type: Literal["simulated"] = "simulated"


class StartConditions(Contract):
    materials: Literal["reported_available", "unavailable", "unknown"]
    time: Literal["reported_30_minutes", "unavailable", "unknown"]
    accompaniment: Literal["reported_present", "unavailable", "unknown"]
    raw_report: str = Field(min_length=1)
    explicit_start: bool


class StartRequest(ParentRequest):
    course_ref: RevisionRef
    conditions: StartConditions


class CourseRequest(ParentRequest):
    course_ref: RevisionRef


class DiscussRequest(CourseRequest):
    feedback: str = Field(min_length=1, max_length=2000)


class ResourceRequest(CourseRequest):
    resource_id: str = Field(min_length=1, max_length=200)


class AdoptRequest(ResourceRequest):
    version: int = Field(ge=1)


class FeedbackRequest(ParentRequest):
    activity_id: str
    text: str = Field(min_length=1)
    corrections: tuple[dict[str, JsonValue], ...] = ()


class ActivityRequest(ParentRequest):
    activity_id: str


class LostReportRequest(ActivityRequest):
    text: str = Field(min_length=1)


class EvaluationRequest(ActivityRequest):
    purpose: Literal["next_course", "workbench_evaluation", "final_feedback"]


class ReviewRequest(ParentRequest):
    level: Literal["week", "stage", "annual", "long_term"]
    proposed_patch: dict[str, JsonValue] | None = None


class DecisionRequest(ParentRequest):
    decision_ref: RevisionRef
    choice: Literal["accept", "reject"]


class CorrectionRequest(ParentRequest):
    target_ref: RevisionRef
    owner: str
    note: str = Field(min_length=1)


class CatalogRequest(ParentRequest):
    capability: Capability


class ReleaseRequest(ParentRequest):
    restriction_ref: RevisionRef
    owner: str
    evidence: str = Field(min_length=1)


class TextInput(Contract):
    event_id: str
    text: str = Field(min_length=1)
    intent: Literal["answer", "help", "confirmation", "report", "unknown"] = "unknown"
    relation: Literal["new", "supplement"] = "new"
    related_event_id: str | None = None


# 300 s of 16 kHz mono PCM16 WAV is 9,600,044 bytes.
MAX_RECORDING_BYTES = 10 * 1024 * 1024


class RecordingInput(Contract):
    """One reading segment's recording; it goes only to pronunciation scoring (ADR-0014)."""

    event_id: str
    # Empty when nothing was read; scoring then returns unknown rather than a low score.
    audio_base64: str = Field(max_length=MAX_RECORDING_BYTES * 4 // 3 + 4)
    input_started_ms: float = Field(ge=0, allow_inf_nan=False)
    input_ended_ms: float = Field(ge=0, allow_inf_nan=False)
    recording_ms: float = Field(ge=0, allow_inf_nan=False)
    pause_count: int = Field(default=0, ge=0)
    completion_trigger: Literal["explicit", "max_duration"] = "explicit"


class Control(Contract):
    event_id: str
    activity_id: str | None = None
    command: Literal[
        "stop", "pause", "resume", "return", "skip", "repeat", "close", "end_unconfirmed"
    ]


class StudentControl(Contract):
    """Student role: stop/pause at once, ask to repeat, report return; flow control is parental."""

    event_id: str
    command: Literal["stop", "pause", "return", "repeat"]


class PlaybackReport(Contract):
    """How the page played one sentence; diagnosis only, nothing of what was said."""

    action_id: str
    # webaudio streams live speech; element plays a whole recording as one file.
    player: Literal["webaudio", "element"]
    outcome: Literal["completed", "failed", "interrupted"]
    sample_rate: int = Field(gt=0)
    reads: int = Field(ge=0)  # Network reads, one audio buffer each.
    late: int = Field(ge=0)  # Buffers that arrived after the previous one had ended.
    # From the browser's playback statistics, when it has them.
    underruns: int | None = Field(default=None, ge=0)
    underrun_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Snapshot(Contract):
    activity_id: str
    learner_id: str
    realm: Literal["simulation", "adult_check"]
    source_type: Literal["simulated", "adult"]
    mode: str
    activity_state: Literal["running", "offscreen", "paused", "closing", "ended"]
    wait_kind: str
    task_instance_id: str
    wait_id: str
    core_revision: int
    cancellation_epoch: int
    input_policy_ref: RevisionRef
    pending_action: AllowedAction | None
    presented_action: AllowedAction | None = None
    attempt_count: int
    elapsed_seconds: float
    end: dict[str, JsonValue] | None
    actual_api: Literal["未验证"]
    actual_voice: Literal["未验证"]
    recording: dict[str, JsonValue] | None = None
    materials: dict[str, str] = {}


class Dashboard(Contract):
    realm: Literal["simulation", "adult_check"] = "simulation"
    learner_id: str = "fictional-L1"
    qualification: Literal["not_granted"] = "not_granted"
    actual_api: Literal["未验证"] = "未验证"
    actual_voice: Literal["未验证"] = "未验证"
    verification: dict[str, JsonValue] = {}
    objects: list[Document] = []
    review_due: list[dict[str, JsonValue]] = []
    model_calls: list[dict[str, JsonValue]] = []
    models: dict[str, JsonValue] = {}
    pending_seals: list[str] = []
    seal_errors: dict[str, str] = {}
    lost_activities: list[dict[str, JsonValue]] = []
    active: Snapshot | None = None
    note: str | None = None


class PreparationResult(Contract):
    course_ref: RevisionRef
    preview_ref: RevisionRef
    check_ref: RevisionRef
    preparation: Literal["drafted"]
    start: Literal["pending"]
    unverified: tuple[str, ...]


class ResourceResult(Contract):
    course_ref: RevisionRef
    preview_ref: RevisionRef
    resource_id: str
    version: int
    state: Literal["generated", "adopted"]


class StartResult(Contract):
    decision: Literal[
        "allow_simulation", "allow_adult_check", "waiting", "blocked", "interrupted_unknown"
    ]
    student_token: str | None = None
    snapshot: Snapshot | None = None
    missing: tuple[str, ...] = ()
    reason: str | None = None
    unverified: tuple[str, ...] = ()


class InputResult(Contract):
    ack: str
    snapshot: Snapshot
    event_id: str | None = None


log = logging.getLogger(__name__)


def describe(error: Exception) -> str:
    """Error kind and message for logs; validation and database messages echo stored content."""
    if isinstance(error, ValidationError):
        return f"ValidationError({error.error_count()} errors)"
    if isinstance(error, SQLAlchemyError):
        return type(error).__name__
    return f"{type(error).__name__}: {str(error)[:200]}"


class AccessLog:
    """One log line per API request after its response (streams included) has finished."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        started, status = time.perf_counter(), 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except Exception as error:
            # Frames only: an exception's message may quote request content.
            log.error(
                "unhandled %s %s %s\n%s",
                scope["method"],
                scope["path"],
                type(error).__name__,
                "".join(traceback.format_tb(error.__traceback__)),
            )
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            log.info("%s %s %d %.0fms", scope["method"], scope["path"], status, elapsed)


def build_coordinator(settings: Settings) -> Coordinator:
    """Simulation keeps its simulated providers; the adult check binds the real local ones."""
    root = settings.project_root
    if settings.realm == "simulation":
        return Coordinator(SimulationStore(root / settings.simulation_root, settings.run_id), root)
    return Coordinator(
        SimulationStore(root / settings.adult_check_root, settings.run_id, realm="adult_check"),
        root,
        models=load_service(ModelFiles(root / "project_j/private/models")),
        scorer=LocalScorer(root),
        synthesizer=LocalSynthesizer(root),
    )


async def speech_body(stream: AsyncIterator[bytes], first: bytes) -> AsyncIterator[bytes]:
    """The approved sentence's audio; closing it (client gone, child cut in) cancels synthesis."""
    async with aclosing(stream):
        yield first
        async for chunk in stream:
            yield chunk


def create_app(
    settings: Settings | None = None,
    coordinator: Coordinator | None = None,
    announce: Sequence[str] = (),
):
    """announce: lines for the person at the terminal, printed once the service has started."""
    settings = settings or Settings()
    static: dict[str, tuple[bytes, str]] = {}
    seal_errors: dict[str, str] = {}
    seal_attempted: set[str] = set()
    connections: dict[WebSocket, str] = {}

    @asynccontextmanager
    async def lifespan(app):
        app.state.coordinator = coordinator or build_coordinator(settings)
        app.state.coordinator.initialize()
        # Local scorer and synthesizer load once here (seconds each), not at every class start.
        # A failed start keeps the server up; class admission then reports the reason.
        local = (app.state.coordinator.scorer, app.state.coordinator.synthesizer)
        for part in local:
            if hasattr(part, "start"):
                try:
                    await part.start()
                except (ScorerUnavailable, SynthesizerUnavailable) as error:
                    log.warning("%s", error)
        for adapter in app.state.coordinator.models.adapters.values():
            if hasattr(adapter, "prewarm"):
                await adapter.prewarm()
        dist = settings.project_root / "project_j/web/dist"
        types = {
            ".html": "text/html",
            ".js": "text/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
        }
        if dist.exists():
            for path in dist.rglob("*"):
                if path.is_file():
                    static[str(path.relative_to(dist))] = (
                        path.read_bytes(),
                        types.get(path.suffix, "application/octet-stream"),
                    )

        async def watch_budget():
            last_binding = None
            while True:
                await asyncio.sleep(1)
                core = app.state.coordinator.core
                if core:
                    if core.mode != "ended":
                        core.tick()
                    if core.mode == "ended":
                        if app.state.coordinator.store.active_activity_id:
                            app.state.coordinator.freeze_end()
                        if core.activity_id not in seal_attempted:
                            await save_after_end()
                    binding = (core.activity_id, core.revision)
                    if binding != last_binding:
                        last_binding = binding
                        for socket, activity_id in tuple(connections.items()):
                            try:
                                if activity_id != core.activity_id:
                                    await socket.close(code=1008)
                                    connections.pop(socket, None)
                                else:
                                    await socket.send_json(
                                        {"snapshot": app.state.coordinator.student_view(core)}
                                    )
                            except RuntimeError, WebSocketDisconnect:
                                connections.pop(socket, None)

        budget_task = asyncio.create_task(watch_budget())
        for line in announce:
            print(line, flush=True)
        try:
            yield
        finally:
            budget_task.cancel()
            try:
                await budget_task
            except asyncio.CancelledError:
                pass
            app_controller = app.state.coordinator
            if app_controller.core and app_controller.core.mode != "ended":
                app_controller.core.finish("interrupted")
                app_controller.store.active_activity_id = None
                try:
                    app_controller.seal()
                except OSError, ValueError:
                    pass
            app_controller.store.close()
            for part in (*local, *app_controller.models.adapters.values()):
                if hasattr(part, "aclose"):
                    await part.aclose()

    app = FastAPI(
        title="Project J 隔离模拟",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(AccessLog)

    def controller():
        return app.state.coordinator

    def parent(authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {controller().parent_token}":
            raise HTTPException(403, "parent capability required")
        return controller()

    def student(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(403, "student activity capability required")
        return controller().student(authorization.removeprefix("Bearer "))

    StudentCore = Annotated[Harness, Depends(student)]

    def refused(request, error, status_code: int, content: dict) -> JSONResponse:
        log.warning(
            "refused %s %s %d %s", request.method, request.url.path, status_code, describe(error)
        )
        return JSONResponse(status_code=status_code, content=content)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return refused(
            request,
            error,
            409 if isinstance(error, Conflict) else 400,
            {
                "message": str(error),
                "current": error.current.model_dump()
                if isinstance(error, Conflict) and error.current
                else None,
            },
        )

    @app.exception_handler(EvaluationUnavailable)
    async def evaluation_failed(request, error):
        return refused(request, error, 503, {"message": str(error), "evaluation": "failed"})

    @app.exception_handler(PlanningUnavailable)
    async def planning_failed(request, error):
        return refused(request, error, 503, {"message": str(error), "planning": "failed"})

    @app.exception_handler(PreparationUnavailable)
    async def preparation_failed(request, error):
        return refused(request, error, 503, {"message": str(error), "preparation": "failed"})

    @app.exception_handler(LookupError)
    async def not_found(request, error):
        return refused(request, error, 404, {"message": str(error)})

    @app.exception_handler(PermissionError)
    async def forbidden(request, error):
        return refused(request, error, 403, {"message": str(error)})

    @app.exception_handler(StorageBlocked)
    async def classroom_storage_closed(request, error):
        return refused(request, error, 423, {"message": str(error)})

    @app.exception_handler(OSError)
    @app.exception_handler(SQLAlchemyError)
    async def save_unknown(request, error):
        return refused(
            request,
            error,
            503,
            {"message": "保存或回执未取得，请查询原操作，不要更换身份重建。", "save": "unknown"},
        )

    async def save_after_end():
        app_controller = controller()
        if app_controller.core and app_controller.core.mode == "ended":
            seal_attempted.add(app_controller.core.activity_id)
            try:
                app_controller.seal()
                seal_errors.pop(app_controller.core.activity_id, None)
            except (OSError, ValueError, SQLAlchemyError) as error:
                seal_errors[app_controller.core.activity_id] = type(error).__name__
                log.warning(
                    "seal failed activity=%s %s", app_controller.core.activity_id, describe(error)
                )

    def ended(core, background: BackgroundTasks):
        if core.mode == "ended":
            controller().freeze_end()
            # Response acknowledges stopping before the independent disk operation.
            background.add_task(save_after_end)

    @app.get("/api/parent/dashboard", dependencies=[Depends(parent)], response_model=Dashboard)
    async def dashboard(
        learner_id: Annotated[str, Query(pattern=r"^fictional-[A-Za-z0-9_-]+$")] = "fictional-L1",
    ):
        app_controller = controller()
        if app_controller.store.active_activity_id:
            if app_controller.core.learner_id != learner_id:
                raise PermissionError(
                    "active memory status does not belong to the requested learner"
                )
            return {
                "realm": app_controller.store.realm,
                "learner_id": learner_id,
                "active": app_controller.core.snapshot(),
                "verification": app_controller.verification,  # held in memory since startup
                "models": app_controller.models.status(),
                "objects": [],
                "note": "课堂进行中，工作台只返回内存控制状态，不访问数据库。",
            }
        return app_controller.dashboard(learner_id) | {"seal_errors": seal_errors}

    @app.post(
        "/api/parent/prepare", dependencies=[Depends(parent)], response_model=PreparationResult
    )
    async def prepare(request: ParentRequest):
        return await controller().prepare(request.request_id, request.learner_id)

    @app.post(
        "/api/parent/discuss", dependencies=[Depends(parent)], response_model=PreparationResult
    )
    async def discuss(request: DiscussRequest):
        return await controller().discuss(
            request.request_id, request.learner_id, request.course_ref, request.feedback
        )

    @app.post("/api/parent/confirm", dependencies=[Depends(parent)])
    async def confirm(request: CourseRequest):
        return controller().confirm(request.request_id, request.learner_id, request.course_ref)

    @app.post(
        "/api/parent/generate-resource",
        dependencies=[Depends(parent)],
        response_model=ResourceResult,
    )
    async def generate(request: ResourceRequest):
        return await generate_resource(
            controller(),
            request.request_id,
            request.learner_id,
            request.course_ref,
            request.resource_id,
        )

    @app.post(
        "/api/parent/adopt-resource", dependencies=[Depends(parent)], response_model=ResourceResult
    )
    async def adopt(request: AdoptRequest):
        return adopt_resource(
            controller(),
            request.request_id,
            request.learner_id,
            request.course_ref,
            request.resource_id,
            request.version,
        )

    @app.get("/api/parent/resource-file", dependencies=[Depends(parent)])
    async def resource_download(
        object_id: str,
        revision: int,
        resource_id: str,
        version: int,
        learner_id: str = "fictional-L1",
    ):
        data, media_type = resource_file(
            controller(),
            learner_id,
            RevisionRef(object_id=object_id, revision=revision),
            resource_id,
            version,
        )
        # A generated SVG is shown as an image only; sandbox it if opened directly.
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": "sandbox"}
        return Response(data, media_type=media_type, headers=headers)

    @app.post("/api/parent/start", dependencies=[Depends(parent)], response_model=StartResult)
    async def start(request: StartRequest):
        result = controller().start(
            request.request_id,
            request.learner_id,
            request.course_ref,
            request.conditions.model_dump(),
        )
        log.info(
            "start decision=%s missing=%s reason=%s",
            result["decision"],
            result.get("missing"),
            result.get("reason"),
        )
        return result

    @app.post("/api/parent/feedback", dependencies=[Depends(parent)])
    async def feedback(request: FeedbackRequest):
        return controller().feedback(
            request.request_id,
            request.learner_id,
            request.activity_id,
            request.text,
            request.corrections,
        )

    @app.post("/api/parent/later-feedback", dependencies=[Depends(parent)])
    async def later(request: ActivityRequest):
        return controller().later_feedback(
            request.request_id, request.learner_id, request.activity_id
        )

    @app.post("/api/parent/lost-report", dependencies=[Depends(parent)])
    async def lost_report(request: LostReportRequest):
        return controller().lost_report(
            request.request_id, request.learner_id, request.activity_id, request.text
        )

    @app.post("/api/parent/evaluate", dependencies=[Depends(parent)])
    async def evaluate(request: EvaluationRequest):
        result = await controller().evaluate(
            request.request_id, request.learner_id, request.activity_id, request.purpose
        )
        return {"evaluation_ref": result.ref.model_dump(), "body": result.body}

    @app.post("/api/parent/next-course", dependencies=[Depends(parent)])
    async def next_course(request: ActivityRequest):
        return await controller().next_course(
            request.request_id, request.learner_id, request.activity_id
        )

    @app.post("/api/parent/review", dependencies=[Depends(parent)])
    async def review_plan(request: ReviewRequest):
        return await review(
            controller(),
            request.request_id,
            request.learner_id,
            request.level,
            request.proposed_patch,
        )

    @app.post("/api/parent/decision", dependencies=[Depends(parent)])
    async def decision(request: DecisionRequest):
        return decide(
            controller(),
            request.request_id,
            request.learner_id,
            request.decision_ref,
            request.choice,
        )

    @app.post("/api/parent/recheck-demand", dependencies=[Depends(parent)])
    async def recheck_demand(request: ParentRequest):
        return revise_demand_after_plan(controller(), request.request_id, request.learner_id)

    @app.post("/api/parent/correction", dependencies=[Depends(parent)])
    async def correction(request: CorrectionRequest):
        return correct(
            controller(),
            request.request_id,
            request.learner_id,
            request.target_ref,
            request.note,
            request.owner,
        )

    @app.post("/api/parent/capability", dependencies=[Depends(parent)])
    async def capability(request: CatalogRequest):
        return define(controller(), request.request_id, request.learner_id, request.capability)

    @app.get("/api/parent/capability", dependencies=[Depends(parent)])
    async def resolve_capability(subject: str, name: str, learner_id: str = "fictional-L1"):
        result = resolve(controller(), learner_id, subject, name)
        return {"capability_ref": result.ref.model_dump() if result else None}

    @app.post("/api/parent/release", dependencies=[Depends(parent)])
    async def release_restriction(request: ReleaseRequest):
        return release(
            controller(),
            request.request_id,
            request.learner_id,
            request.restriction_ref,
            request.owner,
            request.evidence,
        )

    @app.get("/api/parent/operation/{operation_id}", dependencies=[Depends(parent)])
    async def operation(operation_id: str, learner_id: str = "fictional-L1"):
        return controller().ledger.operation(operation_id, learner_id) | {
            "steps": controller().ledger.steps(operation_id, learner_id)
        }

    @app.post("/api/parent/retry-seal", dependencies=[Depends(parent)])
    async def retry_seal():
        result = controller().seal()
        seal_errors.pop(result.body["activity_id"], None)
        return {"fact_ref": result.ref.model_dump(), "save": "succeeded"}

    @app.post("/api/parent/export", dependencies=[Depends(parent)])
    async def export(request: ParentRequest):
        path = controller().files.export(request.learner_id)
        return {
            "path": str(path.relative_to(controller().store.run)),
            "qualification": "not_granted",
        }

    @app.get("/api/parent/export-file", dependencies=[Depends(parent)])
    async def export_file(path: str):
        from project_j.paths import check_path

        app_controller = controller()
        app_controller.store.ensure_idle()
        if not path.startswith("exports/") or not path.endswith(".json"):
            raise ValueError("only the explicit simulation export can be downloaded")
        file = check_path(app_controller.store.run, path, "simulation")
        return Response(
            file.read_bytes(),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{file.name}"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/parent/backup", dependencies=[Depends(parent)])
    async def backup():
        path = controller().files.backup()
        return {"backup_id": path.name, "scope": "simulation_database_and_fixed_assets"}

    class RestoreRequest(Contract):
        backup_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
        new_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @app.post("/api/parent/restore", dependencies=[Depends(parent)])
    async def restore(request: RestoreRequest):
        app_controller = controller()
        app_controller.store.ensure_idle()
        restored = PrivateFiles.restore(
            app_controller.store.run / "backups" / request.backup_id,
            app_controller.store.root,
            request.new_run_id,
        )
        restored.close()
        return {
            "run_id": request.new_run_id,
            "result": "restored_to_new_simulation",
            "qualification": "not_granted",
        }

    @app.get("/api/student/snapshot", response_model=Snapshot)
    async def snapshot(core: StudentCore):
        return controller().student_view(core)

    @app.post("/api/student/input", response_model=InputResult)
    async def submit(request: TextInput, background: BackgroundTasks, core: StudentCore):
        result = await controller().adapter.text(
            request.event_id,
            request.text,
            request.intent,
            request.relation,
            request.related_event_id,
        )
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

    @app.post("/api/student/recording", response_model=InputResult)
    async def recording(request: RecordingInput, background: BackgroundTasks, core: StudentCore):
        try:
            audio = base64.b64decode(request.audio_base64, validate=True)
        except binascii.Error as error:
            raise ValueError("录音数据无法解码") from error
        result = await controller().adapter.recording(
            request.event_id,
            audio,
            **request.model_dump(exclude={"event_id", "audio_base64"}),
        )
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

    @app.post("/api/student/delivery", response_model=Snapshot)
    async def delivery(request: DeliveryFeedback, core: StudentCore):
        core.delivery(request)
        controller().late_delivery(request)
        return controller().student_view(core)

    @app.post("/api/student/playback", status_code=204)
    async def playback(report: PlaybackReport, core: StudentCore):
        if report.action_id not in core.actions:
            raise LookupError("unknown approved action")
        log.info(
            "playback action=%s player=%s outcome=%s rate=%d reads=%d late=%d underruns=%s "
            "underrun_ms=%s latency_ms=%s",
            report.action_id,
            report.player,
            report.outcome,
            report.sample_rate,
            report.reads,
            report.late,
            report.underruns,
            report.underrun_ms,
            report.latency_ms,
        )

    @app.get("/api/student/speech/{action_id}")
    async def speech(action_id: str, core: StudentCore):
        action = core.actions.get(action_id)
        if action is None:
            raise LookupError("unknown approved action")
        synthesizer, recorded = controller().synthesizer, controller().recorded
        parts = action.speech or (action.text,)
        stream = speak(parts, recorded, synthesizer)
        try:
            first = await anext(stream)
        except (SynthesisFailed, StopAsyncIteration) as error:
            await stream.aclose()
            reason = error.reason if isinstance(error, SynthesisFailed) else "failed"
            return JSONResponse(
                status_code=503, content={"message": f"语音合成失败：{reason}", "reason": reason}
            )
        # A failure after the first chunk aborts the response; the page keeps what it played.
        return StreamingResponse(
            speech_body(stream, first),
            media_type=f"audio/L16;rate={synthesizer.sample_rate};channels=1",
            # All recorded: it arrives at once and the page plays it as one file.
            headers={"X-Speech-Source": "recorded" if set(parts) <= recorded.keys() else "live"},
        )

    @app.get("/api/student/visual/{action_id}/{resource_id}")
    async def visual(action_id: str, resource_id: str, core: StudentCore):
        action = core.actions.get(action_id)
        if action is None or resource_id not in action.visuals:
            raise LookupError("unknown visual of an approved action")
        data, media_type = controller().visuals[resource_id]
        return Response(
            data,
            media_type=media_type,
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "sandbox"},
        )

    @app.post("/api/student/control", response_model=Snapshot)
    async def control(request: StudentControl, background: BackgroundTasks, core: StudentCore):
        core.control(request.event_id, request.command)
        ended(core, background)
        return controller().student_view(core)

    @app.post("/api/parent/control", dependencies=[Depends(parent)])
    async def parent_control(request: Control, background: BackgroundTasks):
        core = controller().core
        if core is None:
            raise LookupError("no active memory core")
        if request.activity_id != core.activity_id:
            raise Conflict("parent control must identify the intended current activity")
        core.control(request.event_id, request.command)
        ended(core, background)
        return core.snapshot()

    @app.websocket("/api/student/events")
    async def events(socket: WebSocket):
        token = socket.headers.get("sec-websocket-protocol", "").removeprefix("project-j.")
        try:
            core = controller().student(token)
        except PermissionError:
            await socket.close(code=1008)
            return
        await socket.accept(subprotocol=f"project-j.{token}")
        connections[socket] = core.activity_id
        log.info("student socket opened activity=%s", core.activity_id)
        send_lock = asyncio.Lock()
        jobs = set()

        async def handle(message):
            try:
                controller().student(token)
                if message["kind"] == "input":
                    request = TextInput.model_validate(message["payload"])
                    await controller().adapter.text(
                        request.event_id,
                        request.text,
                        request.intent,
                        request.relation,
                        request.related_event_id,
                    )
                elif message["kind"] == "control":
                    request = StudentControl.model_validate(message["payload"])
                    core.control(request.event_id, request.command)
                elif message["kind"] == "delivery":
                    feedback = DeliveryFeedback.model_validate(message["payload"])
                    core.delivery(feedback)
                    controller().late_delivery(feedback)
                else:
                    raise ValueError("unknown event kind")
                controller().student(token)
                async with send_lock:
                    await socket.send_json(
                        {"reply_to": message.get("id"), "snapshot": controller().student_view(core)}
                    )
                if core.mode == "ended":
                    controller().freeze_end()
                    await save_after_end()
            except PermissionError:
                await socket.close(code=1008)
                connections.pop(socket, None)
            except (ValueError, LookupError) as error:
                log.warning(
                    "student socket refused kind=%s %s", message.get("kind"), describe(error)
                )
                async with send_lock:
                    await socket.send_json({"reply_to": message.get("id"), "error": str(error)})

        try:
            await socket.send_json({"snapshot": controller().student_view(core)})
            while True:
                message = await socket.receive_json()
                job = asyncio.create_task(handle(message))
                jobs.add(job)
                job.add_done_callback(jobs.discard)
        except WebSocketDisconnect:
            # Disconnect alone neither completes nor restarts an activity.
            pass
        finally:
            connections.pop(socket, None)
            log.info("student socket closed activity=%s", core.activity_id)
            for job in jobs:
                job.cancel()

    @app.get("/{path:path}")
    async def webpage(path: str):
        value = static.get(path or "index.html")
        if value is None:
            raise HTTPException(404, "build the simulation web app before opening it")
        content, mime = value
        return Response(content, media_type=mime, headers={"Cache-Control": "no-store"})

    return app


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Start Project J's loopback simulation, or the adult check with real providers."
    )
    parser.add_argument("--run-id", default="local-simulation")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--adult-check",
        action="store_true",
        help="成人实际检查：真实模型、本机发音评分与本机合成，数据写project_j/private/adult_checks",
    )
    parser.add_argument("--simulation-root", type=Path, default=argparse.SUPPRESS)
    debug = "调试用，只作用于模拟适配器和存储："
    parser.add_argument(
        "--model-delay",
        type=float,
        default=argparse.SUPPRESS,
        help=f"{debug}每次模拟模型调用等待秒数",
    )
    parser.add_argument(
        "--fail-once",
        default=argparse.SUPPRESS,
        help=f"{debug}注入一次失败，如fact:before_commit、after_commit、ledger或model",
    )
    args = vars(parser.parse_args(argv))
    adult = args.pop("adult_check")
    simulation_only = {"simulation_root", "model_delay", "fail_once"} & set(args)
    if adult and simulation_only:
        parser.error("--adult-check 不能与模拟调试选项同用：" + "、".join(sorted(simulation_only)))
    settings = Settings(**args, realm="adult_check" if adult else "simulation")
    coordinator = build_coordinator(settings)
    link = f"http://127.0.0.1:{settings.port}/#parent={coordinator.parent_token}"
    if adult:
        announce = [
            f"成人实际检查工作台：{link}",
            "真实模型、本机发音评分与本机合成；录音只在本机内存交评分，不落盘；儿童入口仍关闭。",
        ]
    else:
        simulated = coordinator.models.adapters["simulated"]
        simulated.delay_seconds = settings.model_delay
        simulated.fail_next = settings.fail_once == "model"
        if settings.fail_once != "model":
            coordinator.store.fail_at = settings.fail_once
        announce = [
            f"隔离模拟工作台：{link}",
            "真实API/声音/儿童入口未验证且关闭；不加载.env、不调用麦克风。",
        ]
        if settings.model_delay or settings.fail_once:
            announce.append(
                f"调试注入：模型延迟{settings.model_delay}秒；"
                f"一次失败{settings.fail_once or '无'}。"
            )
    # Diagnostics go to standard error, metadata only; the app itself writes no log file.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logging.Formatter.converter = time.gmtime  # UTC, the clock of the sealed facts.
    uvicorn.run(
        create_app(settings, coordinator, announce),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_config=None,
    )


def export_schema():
    parser = argparse.ArgumentParser(
        description="Export the Python-owned API schema without starting storage or a server."
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n")
