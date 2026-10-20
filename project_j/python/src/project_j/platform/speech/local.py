"""Local pronunciation scorer (ADR-0015): one resident process of the separate scorer project.

The recording goes through the process's stdin in memory; nothing is written to disk.
"""

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from project_j.platform.speech.contracts import PRODUCT_LIMITS, PronunciationResult, WordScore

log = logging.getLogger(__name__)
MODELS = Path("project_j/private/scorer/models")
INIT = Path("project_j/private/scorer/init.json")
BINARY = Path("project_j/scorer/.venv/bin/project-j-scorer")
LIMITATIONS = ("本机评分器，门槛未校准",)
IDENTITY_KEYS = ("scorer", "version", "thresholds", "model", "revision")


def silence(seconds: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(32000 * seconds))
    return buffer.getvalue()


def identity(ready: dict) -> dict:
    return {key: ready.get(key) for key in IDENTITY_KEYS}


def read_init(root: Path) -> dict | None:
    path = root / INIT
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except OSError, ValueError:
        return {"passed": False, "reasons": ["初始化记录无法读取"]}


def file_reasons(root: Path) -> list[str]:
    manifest = root / MODELS / "manifest.json"
    if not manifest.is_file():
        return ["评分模型未下载，请运行 project-j-scorer-init download"]
    record = read_init(root)
    if record is None:
        return ["评分器尚未探测，请运行 project-j-scorer-init probe"]
    if not record.get("passed"):
        return ["评分探针未通过：" + "；".join(record.get("reasons") or ["原因未记录"])]
    if record.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
        return ["评分模型已改动，请重新运行 project-j-scorer-init probe"]
    return []


class ScorerUnavailable(Exception):
    pass


class LocalScorer:
    simulated = False
    limits = PRODUCT_LIMITS
    limitations = LIMITATIONS

    def __init__(
        self,
        root: Path,
        command: Sequence[str] | None = None,
        *,
        start_timeout: float = 120.0,
        score_timeout: float = 30.0,
        inherit_stderr: bool = True,  # Child errors land in the service log.
    ):
        self.root = root
        self.command = (
            list(command)
            if command
            else [str(root / BINARY), "serve", "--models", str(root / MODELS)]
        )
        self.start_timeout, self.score_timeout = start_timeout, score_timeout
        self.inherit_stderr = inherit_stderr
        self.service, self.version = "local-gop", "unknown"
        self.model: str | None = None
        self.thresholds: str | None = None
        self.ready: dict | None = None
        self.last_stats: dict = {}
        self.process: asyncio.subprocess.Process | None = None
        self.exited = False
        self.reviving: asyncio.Task | None = None
        self.closing = False  # Service shutdown: a process that ends now is not replaced.
        self.lock = asyncio.Lock()

    async def start(self) -> dict:
        # The child gets no API keys and stays offline; only download ever uses the network.
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None if self.inherit_stderr else asyncio.subprocess.DEVNULL,
                env=env,
                limit=1 << 20,
            )
        except OSError as error:
            raise ScorerUnavailable(f"评分进程无法启动：{type(error).__name__}") from error
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), self.start_timeout)
            ready = json.loads(line) if line else {"ready": False, "error": "exited"}
        except TimeoutError:
            ready = {"ready": False, "error": "timeout"}
        except ValueError:
            ready = {"ready": False, "error": "invalid_ready_line"}
        if not ready.get("ready"):
            await self.close_process()
            detail = f"（{ready['detail']}）" if ready.get("detail") else ""
            log.warning(
                "scorer not ready error=%s detail=%s", ready.get("error"), ready.get("detail")
            )
            raise ScorerUnavailable(f"评分进程未就绪：{ready.get('error')}{detail}")
        self.ready, self.exited = ready, False
        self.service, self.version = ready["scorer"], ready["version"]
        self.thresholds = ready["thresholds"]
        self.model = f"{ready['model']}@{ready['revision']}"
        log.info(
            "scorer ready device=%s load_ms=%s model=%s thresholds=%s",
            ready.get("device"),
            ready.get("load_ms"),
            self.model,
            self.thresholds,
        )
        return ready

    async def score(self, audio: bytes, reference_text: str) -> PronunciationResult:
        started = time.perf_counter()
        self.last_stats = {}
        result = await self.exchange(audio, reference_text)
        # Compute is the child's own time; the rest of the round trip is queueing and transfer.
        log.info(
            "scorer status=%s reason=%s audio_bytes=%d round_trip_ms=%.0f compute_ms=%s exited=%s",
            result.status,
            result.reason,
            len(audio),
            (time.perf_counter() - started) * 1000,
            self.last_stats.get("elapsed_ms"),
            self.exited,
        )
        return result

    async def warm(self):
        """Page the model back in while the student reads: after synthesis a first score took
        8–28 s under memory pressure, then under 2 s once warmed (2026-10-20, M3 Pro 18 GB)."""
        started = time.perf_counter()
        result = await self.exchange(silence(), "Warm up.")
        log.info(
            "scorer warm status=%s ms=%.0f exited=%s",
            result.status,
            (time.perf_counter() - started) * 1000,
            self.exited,
        )

    async def exchange(self, audio: bytes, reference_text: str) -> PronunciationResult:
        # ponytail: a dead process comes back in the background (another ~6 s load, the same
        # fixed model of ADR-0015); readings meanwhile score unknown.
        async with self.lock:
            if self.ready is None or self.exited or self.process is None:
                return PronunciationResult.unknown("failed", self)
            request_id = str(uuid4())
            request = {
                "id": request_id,
                "audio": base64.b64encode(audio).decode(),
                "reference": reference_text,
            }
            try:
                self.process.stdin.write(json.dumps(request).encode() + b"\n")
                await self.process.stdin.drain()
                async with asyncio.timeout(self.score_timeout):
                    while True:
                        line = await self.process.stdout.readline()
                        if not line:
                            self.died()
                            return PronunciationResult.unknown("failed", self)
                        reply = json.loads(line)
                        if reply.get("id") == request_id:  # Late replies of cancelled requests.
                            break
            except BrokenPipeError, ConnectionResetError:
                self.died()
                return PronunciationResult.unknown("failed", self)
            except TimeoutError, ValueError:
                return PronunciationResult.unknown("failed", self)
            return self.result(reply)

    def died(self):
        """One new process at a time; without it scoring stayed off until the service restarted."""
        self.exited = True
        if not self.closing and (self.reviving is None or self.reviving.done()):
            self.reviving = asyncio.create_task(self.revive())

    async def revive(self):
        started = time.perf_counter()
        await self.close_process()
        try:
            await self.start()
            outcome = "ready"
        except ScorerUnavailable:
            outcome = "failed"
        log.info(
            "scorer revive outcome=%s ms=%.0f", outcome, (time.perf_counter() - started) * 1000
        )

    def result(self, reply: dict) -> PronunciationResult:
        self.last_stats = {
            "elapsed_ms": reply.get("elapsed_ms"),
            "memory_bytes": reply.get("memory_bytes"),
        }
        try:
            if reply.get("status") != "scored":
                return PronunciationResult.unknown(reply.get("reason") or "failed", self)
            return PronunciationResult(
                status="scored",
                words=tuple(WordScore.model_validate(word) for word in reply["words"]),
                accuracy=reply["accuracy"],
                completeness=reply["completeness"],
                pauses=reply.get("pauses"),
                words_per_minute=reply.get("words_per_minute"),
                service=self.service,
                version=self.version,
                simulated=False,
                model=self.model,
                thresholds=self.thresholds,
                limitations=LIMITATIONS,
            )
        except KeyError, TypeError, ValidationError:
            return PronunciationResult.unknown("failed", self)

    def reasons(self) -> list[str]:
        reasons = file_reasons(self.root)
        if self.ready is None:
            reasons.append("评分进程未启动")
        elif self.reviving and not self.reviving.done():
            reasons.append("评分进程正在重新启动，请稍候")
        elif self.exited or self.process is None or self.process.returncode is not None:
            reasons.append("评分进程已退出")
        else:
            record = read_init(self.root) or {}
            if record.get("identity") and record["identity"] != identity(self.ready):
                reasons.append(
                    "评分器版本、模型或门槛与探针记录不一致，请重新运行 project-j-scorer-init probe"
                )
        return reasons

    async def aclose(self) -> None:
        self.closing = True
        if self.reviving and not self.reviving.done():
            self.reviving.cancel()
            try:
                await self.reviving
            except asyncio.CancelledError:
                pass
        await self.close_process()

    async def close_process(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.exited = True
