"""OpenAI Responses adapter: strict structure, store=false, no SDK retries; the key stays here."""

import asyncio
import base64
import json
import struct
import time
import zlib
from datetime import UTC, datetime
from typing import Literal

import openai
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

# Order matters: a timeout is also a connection error.
FAILURES = (
    (openai.AuthenticationError, "auth"),
    (openai.RateLimitError, "quota"),
    (openai.APITimeoutError, "timeout"),
    (openai.PermissionDeniedError, "unavailable"),
    (openai.NotFoundError, "unavailable"),
    (openai.APIConnectionError, "unavailable"),
)
FAILURE_TEXT = {
    "auth": "密钥无效或未配置",
    "quota": "额度不足或限流",
    "timeout": "超时",
    "unavailable": "模型或服务不可用",
    "refused": "模型拒绝回答",
    "incomplete": "返回不完整",
    "invalid_output": "输出不合结构",
}


class ProbeAnswer(BaseModel):
    verdict: Literal["correct", "incorrect", "unclear"]
    evidence: str
    response: str


class ColorAnswer(BaseModel):
    color: str


PROBE = ModelRequest(
    "classroom_judgment",
    "This is a synthetic technical probe, not a child classroom. "
    "Judge only the given source. Propose one short response; do not change state.",
    {
        "source": "Mia has a toy car. She wants a bridge for her car.",
        "question": "What does Mia want to make?",
        "answer": "a bridge.",
    },
    ProbeAnswer,
    deadline_seconds=60,
)


def solid_png(rgb: tuple[int, int, int] = (255, 0, 0), size: int = 64) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    rows = (b"\x00" + bytes(rgb) * size) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def model_matches(requested: str, actual: str) -> bool:
    # A dated snapshot such as gpt-6.1-sol-2026-09-01 serves the requested alias.
    return actual == requested or (
        actual.startswith(f"{requested}-") and actual[len(requested) + 1 :][:1].isdigit()
    )


def check(requested: str, actual: str | None, matches: bool) -> ConfigCheck:
    if actual is None:
        return ConfigCheck(requested=requested, actual=None, status="unverified")
    return ConfigCheck(
        requested=requested, actual=actual, status="verified" if matches else "mismatch"
    )


def probe_status(check_: ConfigCheck | None) -> ProbeItem:
    if check_ is None or check_.status == "unverified":
        return ProbeItem(status="unverified", detail="返回未标明")
    if check_.status == "mismatch":
        return ProbeItem(status="failed", detail=f"返回{check_.actual}")
    return ProbeItem(status="verified")


class OpenAIAdapter:
    adapter_id = "openai"
    version = "1"
    efforts: tuple[Effort, ...] = ("low", "medium", "high")
    tiers: tuple[Tier, ...] = ("default",)
    simulated = False

    def __init__(self, api_key: str | None, http_client=None):
        self._api_key, self._http_client = api_key, http_client
        self._client: openai.AsyncOpenAI | None = None

    def __repr__(self) -> str:
        return "OpenAIAdapter()"  # Never show the key.

    def client(self) -> openai.AsyncOpenAI:
        # One client per adapter: `async with` would close an injected http client after one call.
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=60,
                http_client=self._http_client,
            )
        return self._client

    async def aclose(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def call(
        self, binding: Binding, instructions: str, content, output: type[BaseModel]
    ) -> AdapterReply:
        if not self._api_key:
            return AdapterReply(data=None, failure="auth")
        try:
            response = await self.client().responses.parse(
                model=binding.model,
                reasoning={"effort": binding.effort},
                service_tier="default",
                store=False,
                max_output_tokens=2048,
                text_format=output,
                input=[
                    {"role": "developer", "content": instructions},
                    {"role": "user", "content": content},
                ],
            )
        except ValidationError, ValueError:
            return AdapterReply(data=None, failure="invalid_output")
        except openai.OpenAIError as error:  # Only the kind; error text may echo the key.
            kind = next((k for cls, k in FAILURES if isinstance(error, cls)), "unavailable")
            return AdapterReply(data=None, failure=kind)
        effort = response.reasoning.effort if response.reasoning else None
        actual = {
            "model": check(
                binding.model, response.model, model_matches(binding.model, response.model or "")
            ),
            "effort": check(binding.effort, effort, effort == binding.effort),
            "service_tier": check(
                "default", response.service_tier, response.service_tier == "default"
            ),
        }
        parsed = response.output_parsed
        failure = (
            "incomplete"
            if response.status == "incomplete"
            else "refused"
            if parsed is None
            else None
        )
        return AdapterReply(
            data=parsed.model_dump() if parsed is not None and failure is None else None,
            failure=failure,
            actual=actual,
            usage=response.usage.model_dump() if response.usage else None,
            request_id=response._request_id,
        )

    async def invoke(self, request: ModelRequest, binding: Binding) -> AdapterReply:
        context = json.dumps(request.context, ensure_ascii=False)
        return await self.call(binding, request.instructions, context, request.output)

    async def probe(
        self, model: str, efforts: tuple[Effort, ...], tiers: tuple[Tier, ...] = ()
    ) -> CapabilityReport:
        items: dict[str, ProbeItem] = {
            "audio": ProbeItem(status="not_probed", detail="语音另行设计")
        }
        calls: list[dict] = []
        replies = {}
        for effort in efforts:
            started = time.perf_counter()
            binding = Binding(adapter=self.adapter_id, model=model, effort=effort)
            reply = await self.invoke(PROBE, binding)
            calls.append(
                {
                    "probe": f"effort:{effort}",
                    "failure": reply.failure,
                    "request_id": reply.request_id,
                    "usage": reply.usage,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            replies[effort] = reply
        first = replies[efforts[0]]
        if first.failure in {"auth", "quota", "unavailable", "timeout"}:
            items["auth"] = ProbeItem(status="failed", detail=FAILURE_TEXT[first.failure])
            for name in (
                "model",
                "structured_output",
                "cancellation",
                "image_input",
                *(f"effort:{e}" for e in efforts),
            ):
                items[name] = ProbeItem(status="not_probed", detail="鉴权或连通未通过")
            return self.report(model, items, calls)
        items["auth"] = ProbeItem(status="verified")
        items["model"] = probe_status(first.actual.get("model"))
        for effort, reply in replies.items():
            items[f"effort:{effort}"] = probe_status(reply.actual.get("effort"))
        broken = next((r.failure for r in replies.values() if r.failure), None)
        items["structured_output"] = (
            ProbeItem(status="failed", detail=FAILURE_TEXT.get(broken, broken))
            if broken
            else ProbeItem(status="verified", detail="严格结构")
        )
        items["cancellation"] = await self.probe_cancel(model, efforts[0])
        items["image_input"] = await self.probe_image(model, efforts[0], calls)
        return self.report(model, items, calls)

    async def probe_cancel(self, model: str, effort: Effort) -> ProbeItem:
        job = asyncio.create_task(
            self.invoke(PROBE, Binding(adapter=self.adapter_id, model=model, effort=effort))
        )
        await asyncio.sleep(0.05)
        started = time.perf_counter()
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass
        elapsed = round((time.perf_counter() - started) * 1000)
        if elapsed > 1000:
            return ProbeItem(status="failed", detail=f"客户端{elapsed}ms才收回")
        return ProbeItem(status="verified", detail=f"客户端{elapsed}ms收回；服务端停止与计费未核实")

    async def probe_image(self, model: str, effort: Effort, calls: list[dict]) -> ProbeItem:
        image = base64.b64encode(solid_png()).decode()
        content = [
            {
                "type": "input_text",
                "text": "What color is this image? Answer with one English word.",
            },
            {"type": "input_image", "image_url": f"data:image/png;base64,{image}"},
        ]
        reply = await self.call(
            Binding(adapter=self.adapter_id, model=model, effort=effort),
            "This is a synthetic technical probe. Report only the image color.",
            content,
            ColorAnswer,
        )
        calls.append(
            {
                "probe": "image_input",
                "failure": reply.failure,
                "request_id": reply.request_id,
                "usage": reply.usage,
            }
        )
        if reply.failure == "unavailable":
            return ProbeItem(status="unsupported", detail="服务拒绝图片输入")
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
