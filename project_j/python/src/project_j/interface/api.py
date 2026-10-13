"""Loopback simulation API with separate parent and per-activity student capabilities."""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
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
from fastapi.responses import JSONResponse, Response
from pydantic import Field, JsonValue
from sqlalchemy.exc import SQLAlchemyError

from project_j.application.configuration import Settings
from project_j.application.coordinator import Coordinator
from project_j.classroom.harness import AllowedAction, DeliveryFeedback, Harness
from project_j.contracts import Contract, Document, RevisionRef
from project_j.curriculum.corrections import correct, release
from project_j.learning.catalog import Capability, define, resolve
from project_j.learning.maintenance import decide, review, revise_demand_after_plan
from project_j.platform.files import PrivateFiles
from project_j.platform.storage import Conflict, SimulationStore, StorageBlocked


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


class FeedbackRequest(ParentRequest):
    activity_id: str
    text: str = Field(min_length=1)
    corrections: tuple[dict[str, JsonValue], ...] = ()


class ActivityRequest(ParentRequest):
    activity_id: str


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


class VoiceSignal(Contract):
    kind: Literal[
        "start",
        "fragment",
        "candidate_end",
        "resume",
        "final",
        "window_elapsed",
        "cancel",
        "revision",
    ]
    item_id: str
    text: str = ""
    candidate_end_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source: Literal["simulated_speaker", "teacher_echo"] = "simulated_speaker"


class Snapshot(Contract):
    activity_id: str
    learner_id: str
    realm: Literal["simulation"]
    source_type: Literal["simulated"]
    mode: str
    activity_state: Literal["running", "offscreen", "paused", "closing", "ended"]
    wait_kind: str
    task_instance_id: str
    wait_id: str
    core_revision: int
    cancellation_epoch: int
    input_policy_ref: RevisionRef
    pending_action: AllowedAction | None
    attempt_count: int
    elapsed_seconds: float
    end: dict[str, JsonValue] | None
    actual_api: Literal["未验证"]
    actual_voice: Literal["未验证"]
    materials: dict[str, str] = {}


class Dashboard(Contract):
    realm: Literal["simulation"] = "simulation"
    learner_id: str = "fictional-L1"
    qualification: Literal["not_granted"] = "not_granted"
    actual_api: Literal["未验证"] = "未验证"
    actual_voice: Literal["未验证"] = "未验证"
    verification: dict[str, JsonValue] = {}
    objects: list[Document] = []
    review_due: list[dict[str, JsonValue]] = []
    model_calls: list[dict[str, JsonValue]] = []
    pending_seals: list[str] = []
    seal_errors: dict[str, str] = {}
    active: Snapshot | None = None
    note: str | None = None


class PreparationResult(Contract):
    course_ref: RevisionRef
    preview_ref: RevisionRef
    check_ref: RevisionRef
    preparation: Literal["fixed_simulation_checked"]
    start: Literal["pending"]
    unverified: tuple[str, ...]


class StartResult(Contract):
    decision: Literal["allow_simulation", "waiting", "blocked", "interrupted_unknown"]
    student_token: str | None = None
    snapshot: Snapshot | None = None
    missing: tuple[str, ...] = ()
    reason: str | None = None
    unverified: tuple[str, ...] = ()


class InputResult(Contract):
    ack: str
    snapshot: Snapshot
    event_id: str | None = None


def create_app(settings: Settings | None = None, coordinator: Coordinator | None = None):
    settings = settings or Settings()
    static: dict[str, tuple[bytes, str]] = {}
    seal_errors: dict[str, str] = {}
    seal_attempted: set[str] = set()
    connections: dict[WebSocket, str] = {}

    @asynccontextmanager
    async def lifespan(app):
        app.state.coordinator = coordinator or Coordinator(
            SimulationStore(
                settings.project_root / "project_j/private/simulation", settings.run_id
            ),
            settings.project_root,
        )
        app.state.coordinator.initialize()
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

    app = FastAPI(
        title="Project J 隔离模拟",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

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

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse(
            status_code=409 if isinstance(error, Conflict) else 400,
            content={
                "message": str(error),
                "current": error.current.model_dump()
                if isinstance(error, Conflict) and error.current
                else None,
            },
        )

    @app.exception_handler(LookupError)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={"message": str(error)})

    @app.exception_handler(PermissionError)
    async def forbidden(request, error):
        return JSONResponse(status_code=403, content={"message": str(error)})

    @app.exception_handler(StorageBlocked)
    async def classroom_storage_closed(request, error):
        return JSONResponse(status_code=423, content={"message": str(error)})

    @app.exception_handler(OSError)
    @app.exception_handler(SQLAlchemyError)
    async def save_unknown(request, error):
        return JSONResponse(
            status_code=503,
            content={
                "message": "保存或回执未取得，请查询原操作，不要更换身份重建。",
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
            except (OSError, ValueError, SQLAlchemyError) as error:
                seal_errors[app_controller.core.activity_id] = type(error).__name__

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
                "realm": "simulation",
                "learner_id": learner_id,
                "active": app_controller.core.snapshot(),
                "objects": [],
                "note": "课堂进行中，工作台只返回内存控制状态，不访问数据库。",
            }
        return app_controller.dashboard(learner_id) | {"seal_errors": seal_errors}

    @app.post(
        "/api/parent/prepare", dependencies=[Depends(parent)], response_model=PreparationResult
    )
    async def prepare(request: ParentRequest):
        return controller().prepare(request.request_id, request.learner_id)

    @app.post("/api/parent/start", dependencies=[Depends(parent)], response_model=StartResult)
    async def start(request: StartRequest):
        return controller().start(
            request.request_id,
            request.learner_id,
            request.course_ref,
            request.conditions.model_dump(),
        )

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
        return review(
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

    @app.post("/api/student/delivery", response_model=Snapshot)
    async def delivery(request: DeliveryFeedback, core: StudentCore):
        core.delivery(request)
        controller().late_delivery(request)
        return controller().student_view(core)

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

    @app.post("/api/student/voice-signal", response_model=InputResult)
    async def voice_signal(request: VoiceSignal, background: BackgroundTasks, core: StudentCore):
        result = await controller().adapter.voice_event(**request.model_dump())
        ended(core, background)
        return result | {"snapshot": controller().student_view(core)}

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
                elif message["kind"] == "voice_signal":
                    await controller().adapter.voice_event(
                        **VoiceSignal.model_validate(message["payload"]).model_dump()
                    )
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


def main():
    parser = argparse.ArgumentParser(
        description="Start Project J's loopback simulation; no real API or microphone."
    )
    parser.add_argument("--run-id", default="local-simulation")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    settings = Settings(run_id=args.run_id, port=args.port)
    coordinator = Coordinator(
        SimulationStore(settings.project_root / "project_j/private/simulation", settings.run_id),
        settings.project_root,
    )
    print(f"隔离模拟工作台：http://127.0.0.1:{settings.port}/#parent={coordinator.parent_token}")
    print("真实API/声音/儿童入口未验证且关闭；不加载.env、不调用麦克风。")
    uvicorn.run(
        create_app(settings, coordinator),
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
