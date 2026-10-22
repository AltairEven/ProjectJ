"""Loopback API for the one operator: management and the class share one capability."""

import argparse
import asyncio
import base64
import binascii
import json
import logging
import signal
import threading
import time
import traceback
import webbrowser
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing, asynccontextmanager, suppress
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
from project_j.application.progress import advance, jobs, next_step, retry, run
from project_j.classroom.harness import AllowedAction, DeliveryFeedback, Harness
from project_j.contracts import Contract, Document, RevisionRef
from project_j.evaluation.assessment import EvaluationUnavailable
from project_j.learning.fixtures import initialize
from project_j.learning.maintenance import decide, review, revise_demand_after_plan
from project_j.learning.onboarding import (
    QUESTIONS,
    ProfileEdit,
    confirm_profile,
    save_chat,
    save_lexile,
)
from project_j.learning.planning import PlanningUnavailable
from project_j.platform.files import PrivateFiles
from project_j.platform.models.config import ModelFiles
from project_j.platform.models.registry import load_service
from project_j.platform.speech.local import LocalScorer, ScorerUnavailable
from project_j.platform.storage import Conflict, SimulationStore, StorageBlocked, hold_run
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.local import LocalSynthesizer, SynthesizerUnavailable
from project_j.platform.tts.recorded import speak
from project_j.preparation.drafting import PreparationUnavailable
from project_j.preparation.resources import adopt_resource, generate_resource, resource_file

LEARNER_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ParentRequest(Contract):
    request_id: str = Field(min_length=1)
    learner_id: str = Field(default="fictional-L1", pattern=LEARNER_PATTERN)


class CourseConfirmRequest(ParentRequest):
    course_ref: RevisionRef


class CourseStartRequest(CourseConfirmRequest):
    materials_ready: bool = False  # Only asked when the course uses things off screen.


class CourseRequest(ParentRequest):
    course_ref: RevisionRef


class DiscussRequest(CourseRequest):
    feedback: str = Field(min_length=1, max_length=2000)


class ResourceRequest(CourseRequest):
    resource_id: str = Field(min_length=1, max_length=200)


class AdoptRequest(ResourceRequest):
    version: int = Field(ge=1)


class FeedbackRequest(ParentRequest):
    """A note on a lesson; an objection is also a correction to recheck (ADR-0018)."""

    activity_id: str
    text: str = Field(min_length=1)
    objection: bool = False


class ActivityRequest(ParentRequest):
    activity_id: str


class LostReportRequest(ActivityRequest):
    text: str = Field(min_length=1)


class AcknowledgeRequest(ActivityRequest):
    evaluation_ref: RevisionRef


class LexileRequest(ParentRequest):
    lexile: str = Field(min_length=1, max_length=20)


class ChatRequest(ParentRequest):
    answers: list[str] = Field(min_length=1, max_length=10)


class ProfileRequest(ParentRequest, ProfileEdit):
    pass


class GoalDecision(ParentRequest):
    demand_ref: RevisionRef
    choice: Literal["accept", "reject"]


class FocusRequest(ParentRequest):
    demand_ref: RevisionRef
    topic: str
    skills: list[str]
    note: str | None = None


class ReviewRequest(ParentRequest):
    level: Literal["week", "stage", "annual", "long_term"]
    proposed_patch: dict[str, JsonValue] | None = None


class DecisionRequest(ParentRequest):
    decision_ref: RevisionRef
    choice: Literal["accept", "reject"]


class TextInput(Contract):
    event_id: str
    text: str = Field(min_length=1)
    intent: Literal["answer", "help", "confirmation", "report", "unknown"] = "unknown"
    relation: Literal["new", "supplement"] = "new"
    related_event_id: str | None = None


class ResponseInput(Contract):
    """A clicked task's answer by display position, or a plea for its next hint (ADR-0020)."""

    event_id: str
    intent: Literal["answer", "help"] = "answer"
    response: dict[str, JsonValue] | None = None


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
        "stop",
        "pause",
        "resume",
        "return",
        "skip",
        "repeat",
        "close",
        "end_unconfirmed",
        "start_reading",
        "finish_reading",
    ]


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
    realm: Literal["simulation", "study"]
    source_type: Literal["simulated", "student"]
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
    task_index: int
    task_count: int
    elapsed_seconds: float
    end: dict[str, JsonValue] | None
    actual_api: Literal["unverified"]
    actual_voice: Literal["unverified"]
    recording: dict[str, JsonValue] | None = None
    # A clicked task as the page shows it (options, cards, columns, blanks); never its answer.
    task: dict[str, JsonValue] | None = None
    materials: dict[str, str] = {}


class NextStep(Contract):
    kind: Literal[
        "lexile",
        "chat",
        "placement",
        "profile",
        "class",
        "interrupted",
        "decision",
        "evaluation",
        "goal",
        "focus",
        "course",
        "resources",
        "waiting",
        "none",
    ]
    subject: dict[str, JsonValue] = {}


class JobRow(Contract):
    name: str
    state: Literal["running", "failed"]
    reason: str | None = None
    progress: str | None = None


class JobRetry(Contract):
    learner_id: str = Field(default="fictional-L1", pattern=LEARNER_PATTERN)
    name: str = Field(min_length=1, max_length=300)


class Dashboard(Contract):
    realm: Literal["simulation", "study"] = "simulation"
    learner_id: str = "fictional-L1"
    # The one thing for the student and the AI work under way (application/progress.py).
    next: NextStep | None = None
    jobs: list[JobRow] = []
    qualification: Literal["not_granted"] = "not_granted"
    actual_api: Literal["unverified"] = "unverified"
    actual_voice: Literal["unverified"] = "unverified"
    verification: dict[str, JsonValue] = {}
    objects: list[Document] = []
    previous_evaluations: list[Document] = []
    review_due: list[dict[str, JsonValue]] = []
    model_calls: list[dict[str, JsonValue]] = []
    models: dict[str, JsonValue] = {}
    pending_seals: list[str] = []
    seal_errors: dict[str, str] = {}
    lost_activities: list[dict[str, JsonValue]] = []
    # Each lesson's averages by task type and ability, oldest first (ADR-0020).
    scores: list[dict[str, JsonValue]] = []
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
    """Simulation keeps its simulated providers; study binds the real local ones."""
    root = settings.project_root
    if settings.realm == "simulation":
        return Coordinator(
            SimulationStore(root / settings.simulation_root, settings.run_id),
            root,
            onboarding=settings.onboarding,
        )
    return Coordinator(
        SimulationStore(root / settings.study_root, settings.run_id, realm="study"),
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
    chat_voice: dict[str, bytes] = {}  # The fixed chat questions, said once ahead.

    async def say_chat_ahead(synthesizer):
        """Live, the first question waited 27 s on a synthesizer paged out after idling."""
        began, failed = time.monotonic(), 0
        for question in QUESTIONS:
            pcm = bytearray()
            try:
                async with aclosing(synthesizer.stream(question)) as live:
                    async for chunk in live:
                        pcm += chunk
                chat_voice[question] = bytes(pcm)
            except SynthesisFailed:
                failed += 1
        log.info(
            "onboarding speech pregenerated count=%d failed=%d elapsed_ms=%d",
            len(chat_voice),
            failed,
            (time.monotonic() - began) * 1000,
        )

    @asynccontextmanager
    async def lifespan(app):
        app.state.coordinator = coordinator or build_coordinator(settings)
        initialize(app.state.coordinator)
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
        learner = app.state.coordinator.learner_id
        chatting = next_step(app.state.coordinator, learner)["kind"] in {"lexile", "chat"}
        ahead = asyncio.create_task(say_chat_ahead(local[1])) if chatting else None
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
            if ahead:
                ahead.cancel()
                await asyncio.gather(ahead, return_exceptions=True)
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
        title="Project J",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(AccessLog)

    def controller():
        return app.state.coordinator

    def operator(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise HTTPException(401, "operator capability required")
        if authorization != f"Bearer {controller().token}":
            raise HTTPException(403, "operator capability required")
        return controller()

    def in_class(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise HTTPException(401, "operator capability required")
        return controller().classroom(authorization.removeprefix("Bearer "))

    ClassCore = Annotated[Harness, Depends(in_class)]

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
            {
                "message": "The save or receipt did not come back. Check the original "
                "request; do not send it again under a new id.",
                "save": "unknown",
            },
        )

    async def save_after_end():
        app_controller = controller()
        if app_controller.core and app_controller.core.mode == "ended":
            seal_attempted.add(app_controller.core.activity_id)
            try:
                app_controller.seal()
                seal_errors.pop(app_controller.core.activity_id, None)
                advance(app_controller, app_controller.core.learner_id)  # Evaluate by itself.
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

    @app.get("/api/dashboard", dependencies=[Depends(operator)], response_model=Dashboard)
    async def dashboard(
        learner_id: Annotated[str | None, Query(pattern=LEARNER_PATTERN)] = None,
    ):
        app_controller = controller()
        learner_id = learner_id or app_controller.learner_id
        if app_controller.store.active_activity_id:
            if app_controller.core.learner_id != learner_id:
                raise PermissionError(
                    "active memory status does not belong to the requested learner"
                )
            return {
                "realm": app_controller.store.realm,
                "learner_id": learner_id,
                "active": app_controller.core.snapshot(),
                "next": next_step(app_controller, learner_id),
                "jobs": jobs(app_controller),
                "verification": app_controller.verification,  # held in memory since startup
                "models": app_controller.models.status(),
                "objects": [],
                "note": "A class is running; the home page shows only the class state.",
            }
        return app_controller.dashboard(learner_id) | {"seal_errors": seal_errors}

    @app.post("/api/jobs/retry", dependencies=[Depends(operator)])
    async def retry_job(request: JobRetry):
        started = retry(controller(), request.learner_id, request.name)
        log.info("job retry name=%s started=%s", request.name, started is not None)
        return {"started": started}

    @app.post(
        "/api/course/discuss", dependencies=[Depends(operator)], response_model=PreparationResult
    )
    async def discuss(request: DiscussRequest):
        return await controller().discuss(
            request.request_id, request.learner_id, request.course_ref, request.feedback
        )

    @app.post(
        "/api/resource/generate",
        dependencies=[Depends(operator)],
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
        "/api/resource/adopt", dependencies=[Depends(operator)], response_model=ResourceResult
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

    @app.get("/api/resource/file", dependencies=[Depends(operator)])
    async def resource_download(
        object_id: str,
        revision: int,
        resource_id: str,
        version: int,
        learner_id: str | None = None,
    ):
        data, media_type = resource_file(
            controller(),
            learner_id or controller().learner_id,
            RevisionRef(object_id=object_id, revision=revision),
            resource_id,
            version,
        )
        # A generated SVG is shown as an image only; sandbox it if opened directly.
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": "sandbox"}
        return Response(data, media_type=media_type, headers=headers)

    def background(learner_id: str, name: str, work) -> dict:
        """Run work(progress) as the background job name; the page follows its n/m progress."""
        app_controller = controller()
        if name not in app_controller.jobs:
            app_controller.job_failures.pop(name, None)

            def progress(done: int, total: int):
                app_controller.job_progress[name] = f"{done}/{total}"

            run(app_controller, learner_id, name, lambda: work(progress))
        return {"job": name}

    @app.post("/api/course/confirm", dependencies=[Depends(operator)], status_code=202)
    async def confirm_course(request: CourseConfirmRequest):
        """Confirm the version and make its resources; the class waits for the student."""
        course = request.course_ref
        return background(
            request.learner_id,
            f"confirm:{course.object_id}:{course.revision}",
            lambda progress: controller().confirm_course(
                request.request_id, request.learner_id, course, progress
            ),
        )

    @app.post("/api/course/start", dependencies=[Depends(operator)], status_code=202)
    async def start(request: CourseStartRequest):
        """Start when the student chooses (confirming and making resources if still needed)."""
        course = request.course_ref
        return background(
            request.learner_id,
            f"start:{course.object_id}:{course.revision}",
            lambda progress: controller().confirm_and_start(
                request.request_id, request.learner_id, course, request.materials_ready, progress
            ),
        )

    @app.post("/api/evaluation/feedback", dependencies=[Depends(operator)])
    async def feedback(request: FeedbackRequest):
        return controller().feedback(
            request.request_id,
            request.learner_id,
            request.activity_id,
            request.text,
            ({"note": request.text},) if request.objection else (),
        )

    @app.post("/api/onboarding/lexile", dependencies=[Depends(operator)])
    async def onboarding_lexile(request: LexileRequest):
        return save_lexile(controller(), request.request_id, request.learner_id, request.lexile)

    @app.get("/api/onboarding/questions", dependencies=[Depends(operator)])
    async def onboarding_questions():
        return list(QUESTIONS)

    @app.post("/api/onboarding/chat", dependencies=[Depends(operator)])
    async def onboarding_chat(request: ChatRequest):
        return save_chat(controller(), request.request_id, request.learner_id, request.answers)

    @app.post("/api/profile", dependencies=[Depends(operator)])
    async def profile_confirmed(request: ProfileRequest):
        edit = ProfileEdit.model_validate(request.model_dump(include=set(ProfileEdit.model_fields)))
        return confirm_profile(controller(), request.request_id, request.learner_id, edit)

    @app.post("/api/goal/decide", dependencies=[Depends(operator)])
    async def decide_goal(request: GoalDecision):
        return await controller().decide_goal(
            request.request_id, request.learner_id, request.demand_ref, request.choice
        )

    @app.post("/api/focus", dependencies=[Depends(operator)])
    async def choose_focus(request: FocusRequest):
        return controller().choose_focus(
            request.request_id,
            request.learner_id,
            request.demand_ref,
            request.topic,
            request.skills,
            request.note,
        )

    @app.post("/api/evaluation/acknowledge", dependencies=[Depends(operator)])
    async def acknowledge(request: AcknowledgeRequest):
        return controller().acknowledge(
            request.request_id, request.learner_id, request.activity_id, request.evaluation_ref
        )

    @app.post("/api/lost-report", dependencies=[Depends(operator)])
    async def lost_report(request: LostReportRequest):
        return controller().lost_report(
            request.request_id, request.learner_id, request.activity_id, request.text
        )

    @app.post("/api/review", dependencies=[Depends(operator)])
    async def review_plan(request: ReviewRequest):
        return await review(
            controller(),
            request.request_id,
            request.learner_id,
            request.level,
            request.proposed_patch,
        )

    @app.post("/api/decision", dependencies=[Depends(operator)])
    async def decision(request: DecisionRequest):
        return decide(
            controller(),
            request.request_id,
            request.learner_id,
            request.decision_ref,
            request.choice,
        )

    @app.post("/api/recheck-demand", dependencies=[Depends(operator)])
    async def recheck_demand(request: ParentRequest):
        return revise_demand_after_plan(controller(), request.request_id, request.learner_id)

    @app.post("/api/retry-seal", dependencies=[Depends(operator)])
    async def retry_seal():
        result = controller().seal()
        seal_errors.pop(result.body["activity_id"], None)
        return {"fact_ref": result.ref.model_dump(), "save": "succeeded"}

    @app.post("/api/export", dependencies=[Depends(operator)])
    async def export(request: ParentRequest):
        path = controller().files.export(request.learner_id)
        return {
            "path": str(path.relative_to(controller().store.run)),
            "qualification": "not_granted",
        }

    @app.get("/api/export-file", dependencies=[Depends(operator)])
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

    @app.post("/api/backup", dependencies=[Depends(operator)])
    async def backup():
        path = controller().files.backup()
        return {"backup_id": path.name, "scope": "simulation_database_and_fixed_assets"}

    class RestoreRequest(Contract):
        backup_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
        new_run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @app.post("/api/restore", dependencies=[Depends(operator)])
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

    @app.get("/api/class/snapshot", response_model=Snapshot)
    async def snapshot(core: ClassCore):
        return controller().student_view(core)

    @app.post("/api/class/input", response_model=InputResult)
    async def submit(request: TextInput, background: BackgroundTasks, core: ClassCore):
        result = await controller().adapter.text(
            request.event_id,
            request.text,
            request.intent,
            request.relation,
            request.related_event_id,
        )
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

    @app.post("/api/class/response", response_model=InputResult)
    async def response(request: ResponseInput, background: BackgroundTasks, core: ClassCore):
        result = await controller().adapter.response(
            request.event_id, request.response, request.intent
        )
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

    @app.post("/api/class/recording", response_model=InputResult)
    async def recording(request: RecordingInput, background: BackgroundTasks, core: ClassCore):
        try:
            audio = base64.b64decode(request.audio_base64, validate=True)
        except binascii.Error as error:
            raise ValueError("The recording could not be decoded.") from error
        result = await controller().adapter.recording(
            request.event_id,
            audio,
            **request.model_dump(exclude={"event_id", "audio_base64"}),
        )
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

    @app.post("/api/class/delivery", response_model=Snapshot)
    async def delivery(request: DeliveryFeedback, core: ClassCore):
        core.delivery(request)
        controller().late_delivery(request)
        return controller().student_view(core)

    @app.post("/api/class/playback", status_code=204)
    async def playback(report: PlaybackReport, core: ClassCore):
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

    @app.get("/api/class/speech/{action_id}")
    async def speech(action_id: str, core: ClassCore):
        action = core.actions.get(action_id)
        if action is None:
            raise LookupError("unknown approved action")
        return await spoken(action.speech or (action.text,), controller().recorded)

    @app.get("/api/onboarding/speech/{index}", dependencies=[Depends(operator)])
    async def onboarding_speech(index: int):
        if not 0 <= index < len(QUESTIONS):
            raise LookupError("no such onboarding question")
        return await spoken((QUESTIONS[index],), chat_voice)

    async def spoken(parts, recorded):
        """Jane's sentence as a stream; whole recorded sentences arrive at once."""
        synthesizer = controller().synthesizer
        stream = speak(parts, recorded, synthesizer)
        try:
            first = await anext(stream)
        except (SynthesisFailed, StopAsyncIteration) as error:
            await stream.aclose()
            reason = error.reason if isinstance(error, SynthesisFailed) else "failed"
            return JSONResponse(
                status_code=503, content={"message": f"Speech failed: {reason}", "reason": reason}
            )
        # A failure after the first chunk aborts the response; the page keeps what it played.
        return StreamingResponse(
            speech_body(stream, first),
            media_type=f"audio/L16;rate={synthesizer.sample_rate};channels=1",
            # All recorded: it arrives at once and the page plays it as one file.
            headers={"X-Speech-Source": "recorded" if set(parts) <= recorded.keys() else "live"},
        )

    @app.get("/api/class/visual/{action_id}/{resource_id}")
    async def visual(action_id: str, resource_id: str, core: ClassCore):
        action = core.actions.get(action_id)
        if action is None or resource_id not in action.visuals:
            raise LookupError("unknown visual of an approved action")
        data, media_type = controller().visuals[resource_id]
        return Response(
            data,
            media_type=media_type,
            headers={"Cache-Control": "no-store", "Content-Security-Policy": "sandbox"},
        )

    @app.post("/api/class/control", response_model=Snapshot)
    async def control(request: Control, background: BackgroundTasks, core: ClassCore):
        if request.activity_id is not None and request.activity_id != core.activity_id:
            raise Conflict("control names another activity than the current class")
        core.control(request.event_id, request.command)
        ended(core, background)
        return controller().student_view(core)

    @app.websocket("/api/class/events")
    async def events(socket: WebSocket):
        token = socket.headers.get("sec-websocket-protocol", "").removeprefix("project-j.")
        try:
            core = controller().classroom(token)
        except PermissionError:
            await socket.close(code=1008)
            return
        await socket.accept(subprotocol=f"project-j.{token}")
        connections[socket] = core.activity_id
        log.info("class socket opened activity=%s", core.activity_id)
        send_lock = asyncio.Lock()
        jobs = set()

        async def handle(message):
            try:
                if controller().classroom(token) is not core:
                    raise PermissionError("the socket's class has ended")
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
                    request = Control.model_validate(message["payload"])
                    core.control(request.event_id, request.command)
                elif message["kind"] == "delivery":
                    feedback = DeliveryFeedback.model_validate(message["payload"])
                    core.delivery(feedback)
                    controller().late_delivery(feedback)
                else:
                    raise ValueError("unknown event kind")
                if controller().classroom(token) is not core:
                    raise PermissionError("the socket's class has ended")
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
                log.warning("class socket refused kind=%s %s", message.get("kind"), describe(error))
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
            log.info("class socket closed activity=%s", core.activity_id)
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


def hang_up(signum, frame):
    """Closing the terminal window stops like Ctrl+C: the core ends the class, then it is sealed."""
    log.info("stop signal=SIGHUP")
    signal.raise_signal(signal.SIGTERM)


def open_when_started(server, link: str):
    """The token is new each start; open the page once the server listens (after speech loads)."""
    while not server.started and not server.should_exit:
        time.sleep(0.2)
    if server.started:
        webbrowser.open(link)
        log.info("browser opened")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Start Project J's loopback simulation, or study with real providers."
    )
    parser.add_argument("--run-id", default="local-simulation")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--open", action="store_true", help="服务开始监听后用默认浏览器打开带令牌的页面"
    )
    parser.add_argument(
        "--study",
        action="store_true",
        help="正式学习：真实模型、本机发音评分与本机合成，数据写project_j/private/study",
    )
    parser.add_argument("--simulation-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="调试用，只作用于模拟：从建档开始，不用固定的模拟学习资料与首个需求",
    )
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
    opening = args.pop("open")
    study = args.pop("study")
    onboarding = args.pop("onboarding")
    simulation_only = {"simulation_root", "model_delay", "fail_once"} & set(args)
    if onboarding:
        simulation_only.add("onboarding")
    if study and simulation_only:
        parser.error("--study 不能与模拟调试选项同用：" + "、".join(sorted(simulation_only)))
    settings = Settings(**args, realm="study" if study else "simulation", onboarding=onboarding)
    coordinator = build_coordinator(settings)
    try:
        hold_run(coordinator.store.run)  # Kept open until the process ends, even on a crash.
    except StorageBlocked as error:
        parser.exit(1, f"{error}\n")
    link = f"http://127.0.0.1:{settings.port}/#token={coordinator.token}"
    if study:
        announce = [
            f"正式学习：{link}",
            "真实模型、本机发音评分与本机合成；录音只在内存交评分，不落盘。",
        ]
    else:
        simulated = coordinator.models.adapters["simulated"]
        simulated.delay_seconds = settings.model_delay
        simulated.fail_next = settings.fail_once == "model"
        if settings.fail_once != "model":
            coordinator.store.fail_at = settings.fail_once
        announce = [
            f"隔离模拟：{link}",
            "模拟提供者；不加载.env、不调用麦克风，内容不是学习记录。",
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
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, coordinator, announce),
            host=settings.host,
            port=settings.port,
            access_log=False,
            log_config=None,
        )
    )
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:  # nohup keeps running.
        signal.signal(signal.SIGHUP, hang_up)
    if opening:
        threading.Thread(target=open_when_started, args=(server, link), daemon=True).start()
    with suppress(KeyboardInterrupt):  # Uvicorn re-raises Ctrl+C after its graceful stop.
        server.run()
    if not server.started:
        raise SystemExit(3)  # As uvicorn.run: the application failed to start.


def export_schema():
    parser = argparse.ArgumentParser(
        description="Export the Python-owned API schema without starting storage or a server."
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n")
