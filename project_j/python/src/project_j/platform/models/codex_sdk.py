"""Codex SDK adapter: ChatGPT subscription through a local codex app-server with its own CODEX_HOME.

Each call is a fresh ephemeral, read-only thread in an empty directory with every escalation
denied; context is assembled by the caller, never kept by Codex.
"""

import asyncio
import base64
import contextlib
import json
import logging
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from openai.lib._pydantic import to_strict_json_schema
from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    CodexConfig,
    CodexError,
    ImageInput,
    Sandbox,
    TextInput,
)
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ImageGenerationThreadItem,
    ItemCompletedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStatus,
)
from pydantic import BaseModel, ValidationError

from project_j.platform.models.contracts import (
    AdapterReply,
    Binding,
    CapabilityReport,
    ConfigCheck,
    Effort,
    ModelRequest,
    ProbeItem,
    Tier,
)
from project_j.platform.models.openai_api import FAILURE_TEXT, PROBE, ColorAnswer, solid_png

log = logging.getLogger(__name__)
ERRORS = {
    "unauthorized": "auth",
    "usageLimitExceeded": "quota",
    "rateLimitExceeded": "quota",
    "sessionBudgetExceeded": "quota",
    "cyberPolicy": "refused",
    "misalignmentPolicyViolation": "refused",
}
# Startup, transport and SDK failures; their text may carry account details: keep the kind only.
RUNTIME_ERRORS = (CodexError, OSError, RuntimeError)
LOGIN_HINT = "未登录，请运行 project-j-init login codex"
# Each call stands alone: no memories or history kept; no project docs, web search, analytics,
# apps, plugins or skills, and as little built-in context as the runtime allows.
OVERRIDES = (
    "memories.generate_memories=false",
    "memories.use_memories=false",
    'history.persistence="none"',
    "project_doc_max_bytes=0",
    'web_search="disabled"',
    "analytics.enabled=false",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "include_environment_context=false",
    "include_permissions_instructions=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "features.apps=false",
    "features.plugins=false",
    "features.skills=false",
)
# Replaces Codex's coding-agent prompt; the request's own instructions follow as developer text.
BASE_INSTRUCTIONS = (
    "Answer the single request in the developer and user messages. "
    "Use no tools; reply only with JSON matching the required schema."
)
# A picture request may use the built-in image tool (features.image_generation) once.
IMAGE_INSTRUCTIONS = (
    "Use the image generation tool exactly once for the single request in the developer and "
    "user messages, then reply only with JSON matching the required schema."
)
IMAGE_TOOL = "imageGeneration"
# Turn items that are not tool use; anything else means tools were not actually closed.
QUIET_ITEMS = {"userMessage", "agentMessage", "reasoning", "plan", "contextCompaction"}
# Subscription service tier ids; the default tier sends none.
SERVICE_TIERS = {"fast": "priority"}


def codex_config(home: Path, workdir: Path) -> CodexConfig:
    return CodexConfig(env={"CODEX_HOME": str(home)}, cwd=str(workdir), config_overrides=OVERRIDES)


def error_kind(turn) -> str:
    info = turn.error.codex_error_info if turn.error else None
    value = getattr(info.root, "value", None) if info is not None else None
    return ERRORS.get(value, "unavailable")


async def collect(handle):
    """Final answer, completed turn, usage, tool item types and generated pictures."""
    text, turn, usage, tools, images = None, None, None, set(), []
    async for event in handle.stream():
        payload = event.payload
        if isinstance(payload, ItemCompletedNotification):
            item = getattr(payload.item, "root", payload.item)
            if isinstance(item, AgentMessageThreadItem):
                text = item.text
            elif item.type not in QUIET_ITEMS:
                tools.add(item.type)
                if isinstance(item, ImageGenerationThreadItem):
                    images.append(item)
        elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
            usage = payload.token_usage.model_dump(mode="json")
        elif isinstance(payload, TurnCompletedNotification) and payload.turn.id == handle.id:
            turn = payload.turn
    return text, turn, usage, tools, images


def picture(item: ImageGenerationThreadItem) -> bytes:
    """The image bytes, inline or from the file the runtime saved under its home.

    The runtime saves every picture there; the copy and its emptied folder are removed, so
    course pictures live only in the project's own storage.
    """
    path = Path(item.saved_path.root) if item.saved_path else None
    try:
        if item.result:
            return base64.b64decode(item.result)
        return path.read_bytes() if path else b""
    finally:
        if path:
            path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                path.parent.rmdir()


class CodexAdapter:
    adapter_id = "codex"
    version = "2"
    efforts: tuple[Effort, ...] = ("low", "medium", "high", "xhigh")
    tiers: tuple[Tier, ...] = ("default", "fast")
    simulated = False

    def __init__(self, home: Path, factory=None):
        self.home = home.absolute()  # The runtime runs elsewhere; keep the home fixed.
        self._factory = factory or self.launch
        self._codex = None
        self._workdir: Path | None = None

    def __repr__(self) -> str:
        return "CodexAdapter()"

    @staticmethod
    def logged_in(home: Path) -> bool:
        return (home / "auth.json").exists()

    def launch(self, workdir: Path) -> AsyncCodex:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home.chmod(0o700)
        return AsyncCodex(codex_config(self.home, workdir))

    def codex(self):
        if self._codex is None:
            self._workdir = Path(tempfile.mkdtemp(prefix="project-j-codex-"))
            self._codex = self._factory(self._workdir)
            log.info("codex runtime launched")  # It starts with the first thread.
        return self._codex

    async def aclose(self):
        if self._codex is not None:
            with contextlib.suppress(*RUNTIME_ERRORS):
                await self._codex.close()
            self._codex = None
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    async def login(self, show) -> bool:
        handle = await self.codex().login_chatgpt()
        show(f"请在本机浏览器打开以下链接完成 ChatGPT 登录：\n{handle.auth_url}")
        return (await handle.wait()).success

    async def call(
        self,
        binding: Binding,
        instructions: str,
        input,
        output: type[BaseModel],
        timeout=60.0,
        media=None,
    ) -> AdapterReply:
        timing: dict[str, float | None] = {"thread": None, "turn": None}
        started, reply = time.perf_counter(), None
        try:
            reply = await self.exchange(
                binding, instructions, input, output, timeout, timing, media
            )
            return reply
        finally:
            # Thread time includes the runtime's own start on the first call of a process.
            log.info(
                "codex thread_ms=%s turn_ms=%s total_ms=%.0f status=%s",
                timing["thread"],
                timing["turn"],
                (time.perf_counter() - started) * 1000,
                "cancelled" if reply is None else reply.failure or "completed",
            )

    async def exchange(
        self, binding, instructions, input, output, timeout, timing, media=None
    ) -> AdapterReply:
        actual = {
            "model": ConfigCheck(requested=binding.model, actual=None, status="unverified"),
            "effort": ConfigCheck(requested=binding.effort, actual=None, status="unverified"),
        }
        if binding.tier != "default":  # The runtime does not report the tier it served.
            actual["service_tier"] = ConfigCheck(
                requested=binding.tier, actual=None, status="unverified"
            )
        handle, begun = None, time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                codex = self.codex()
                thread = await codex.thread_start(
                    model=binding.model,
                    ephemeral=True,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    base_instructions=IMAGE_INSTRUCTIONS if media else BASE_INSTRUCTIONS,
                    developer_instructions=instructions,
                    cwd=str(self._workdir),
                    service_tier=SERVICE_TIERS.get(binding.tier),
                )
                timing["thread"] = round((time.perf_counter() - begun) * 1000)
                handle = await thread.turn(
                    input, effort=binding.effort, output_schema=to_strict_json_schema(output)
                )
                text, turn, usage, tools, images = await collect(handle)
                timing["turn"] = round((time.perf_counter() - begun) * 1000) - timing["thread"]
        except (asyncio.CancelledError, TimeoutError) as stop:
            if handle is not None:  # Stop the server-side turn too, not only our wait.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(handle.interrupt(), 1)
            if isinstance(stop, asyncio.CancelledError):
                raise
            return AdapterReply(data=None, failure="timeout", actual=actual)
        except RUNTIME_ERRORS:
            return AdapterReply(data=None, failure="unavailable", actual=actual)
        allowed = {IMAGE_TOOL} if media else set()
        used = ",".join(sorted(tools)) or "none"
        actual["tools"] = ConfigCheck(
            requested=IMAGE_TOOL if media else "none",
            actual=used,
            status="mismatch" if tools - allowed else "verified",
        )
        if turn is not None and turn.status is TurnStatus.failed:
            return AdapterReply(data=None, failure=error_kind(turn), actual=actual)
        if any(item.failure is not None for item in images):
            return AdapterReply(data=None, failure="quota", actual=actual)
        pictures = tuple(picture(item) for item in images) if media else ()
        if (
            turn is None
            or turn.status is not TurnStatus.completed
            or text is None
            or (media and not any(pictures))
        ):
            return AdapterReply(data=None, failure="incomplete", actual=actual)
        try:
            data = output.model_validate_json(text).model_dump()
        except ValidationError:
            return AdapterReply(data=None, failure="invalid_output", actual=actual)
        return AdapterReply(
            data=data, actual=actual, usage=usage, request_id=turn.id, media=pictures
        )

    async def invoke(self, request: ModelRequest, binding: Binding) -> AdapterReply:
        context = json.dumps(request.context, ensure_ascii=False)
        return await self.call(
            binding,
            request.instructions,
            context,
            request.output,
            request.deadline_seconds,
            request.media,
        )

    async def probe(
        self, model: str, efforts: tuple[Effort, ...], tiers: tuple[Tier, ...] = ()
    ) -> CapabilityReport:
        items = {"audio": ProbeItem(status="not_probed", detail="语音另行设计")}
        calls: list[dict] = []
        rest = ("model", "structured_output", "tools", "cancellation", "image_input")
        rest = (*rest, *(f"effort:{e}" for e in efforts), *(f"tier:{t}" for t in tiers))
        try:
            account = await self.codex().account()
            listed = await self.codex().models()
        except RUNTIME_ERRORS:
            account, listed = None, None
        if account is None or account.account is None:
            detail = "Codex 运行时无法启动" if account is None else LOGIN_HINT
            items["auth"] = ProbeItem(status="failed", detail=detail)
            items.update(
                {name: ProbeItem(status="not_probed", detail="鉴权未通过") for name in rest}
            )
            return self.report(model, items, calls)
        items["auth"] = ProbeItem(status="verified", detail="ChatGPT 订阅登录")
        entry = next((m for m in listed.data if model in {m.id, m.model}), None)
        items["model"] = (
            ProbeItem(status="verified", detail="订阅模型列表包含；每次实际模型返回未标明")
            if entry
            else ProbeItem(status="failed", detail="订阅模型列表不含该模型")
        )
        supported = (
            {o.reasoning_effort.value for o in entry.supported_reasoning_efforts or []}
            if entry
            else set()
        )
        offered = {t.id for t in entry.service_tiers or []} if entry else set()
        for tier in tiers:
            items[f"tier:{tier}"] = (
                ProbeItem(status="unverified", detail="订阅列表声明支持；实际层级返回未标明")
                if SERVICE_TIERS[tier] in offered
                else ProbeItem(status="unsupported", detail="订阅模型列表未声明该层级")
            )
        broken, tool_checks = None, []
        for effort in efforts:
            started = time.perf_counter()
            reply = await self.invoke(
                PROBE, Binding(adapter=self.adapter_id, model=model, effort=effort)
            )
            calls.append(
                {
                    "probe": f"effort:{effort}",
                    "failure": reply.failure,
                    "request_id": reply.request_id,
                    "usage": reply.usage,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            broken = broken or reply.failure
            tool_checks.append(reply.actual.get("tools"))
            items[f"effort:{effort}"] = (
                ProbeItem(status="unverified", detail="列表声明支持；实际档位返回未标明")
                if effort in supported
                else ProbeItem(status="unsupported", detail="订阅模型列表未声明该档位")
            )
        items["structured_output"] = (
            ProbeItem(status="failed", detail=FAILURE_TEXT.get(broken, broken))
            if broken
            else ProbeItem(status="verified", detail="严格结构")
        )
        leaked = {c.actual for c in tool_checks if c is not None and c.status == "mismatch"}
        items["tools"] = (
            ProbeItem(status="failed", detail=f"出现工具调用：{','.join(sorted(leaked))}")
            if leaked
            else ProbeItem(status="verified", detail="探测各轮未出现工具调用")
            if all(tool_checks)
            else ProbeItem(status="unverified", detail="调用未完成")
        )
        items["cancellation"] = await self.probe_cancel(model, efforts[0])
        items["image_input"] = await self.probe_image(model, efforts[0], calls)
        return self.report(model, items, calls)

    async def probe_cancel(self, model: str, effort: Effort) -> ProbeItem:
        binding = Binding(adapter=self.adapter_id, model=model, effort=effort)
        job = asyncio.create_task(self.invoke(PROBE, binding))
        await asyncio.sleep(0.5)  # Let the turn start so the interrupt reaches the server.
        started = time.perf_counter()
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job
        elapsed = round((time.perf_counter() - started) * 1000)
        if elapsed > 2000:
            return ProbeItem(status="failed", detail=f"{elapsed}ms才收回")
        return ProbeItem(status="verified", detail=f"已请求服务端中断，{elapsed}ms收回；计费未核实")

    async def probe_image(self, model: str, effort: Effort, calls: list[dict]) -> ProbeItem:
        image = base64.b64encode(solid_png()).decode()
        reply = await self.call(
            Binding(adapter=self.adapter_id, model=model, effort=effort),
            "This is a synthetic technical probe. Report only the image color.",
            [
                TextInput("What color is this image? Answer with one English word."),
                ImageInput(f"data:image/png;base64,{image}"),
            ],
            ColorAnswer,
        )
        calls.append({"probe": "image_input", "failure": reply.failure, "usage": reply.usage})
        if reply.failure:
            return ProbeItem(status="failed", detail=FAILURE_TEXT.get(reply.failure, reply.failure))
        color = str(reply.data.get("color", ""))
        if "red" in color.casefold():
            return ProbeItem(status="verified")
        return ProbeItem(status="failed", detail=f"识别为{color}")

    def report(
        self, model: str, items: dict[str, ProbeItem], calls: list[dict]
    ) -> CapabilityReport:
        return CapabilityReport(
            adapter=self.adapter_id,
            adapter_version=self.version,
            model=model,
            simulated=False,
            captured_at=datetime.now(UTC).isoformat(),
            items=items,
            calls=calls,
        )
