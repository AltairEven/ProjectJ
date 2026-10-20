"""Local speech synthesis (ADR-0016): one resident process of the separate TTS project.

Text and audio travel through the process's stdin/stdout in memory; nothing is written to disk.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from project_j.platform.tts.contracts import SynthesisFailed

log = logging.getLogger(__name__)
MODELS = Path("project_j/private/tts/models")
INIT = Path("project_j/private/tts/init.json")
BINARY = Path("project_j/tts/.venv/bin/project-j-tts")
IDENTITY_KEYS = ("synthesizer", "version", "voice", "model", "revision")
REFUSALS = ("empty_text", "too_long")


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
        return ["合成模型未下载，请运行 project-j-tts-init download"]
    record = read_init(root)
    if record is None:
        return ["合成器尚未探测，请运行 project-j-tts-init probe"]
    if not record.get("passed"):
        return ["合成探针未通过：" + "；".join(record.get("reasons") or ["原因未记录"])]
    if record.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
        return ["合成模型已改动，请重新运行 project-j-tts-init probe"]
    return []


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class SynthesizerUnavailable(Exception):
    pass


class LocalSynthesizer:
    simulated = False

    def __init__(
        self,
        root: Path,
        command: Sequence[str] | None = None,
        *,
        start_timeout: float = 180.0,
        chunk_timeout: float = 30.0,
        cancel_timeout: float = 3.0,
        inherit_stderr: bool = True,  # Child errors land in the service log.
    ):
        self.root = root
        self.command = (
            list(command)
            if command
            else [str(root / BINARY), "serve", "--models", str(root / MODELS)]
        )
        self.start_timeout, self.chunk_timeout = start_timeout, chunk_timeout
        self.cancel_timeout = cancel_timeout
        self.inherit_stderr = inherit_stderr
        self.service, self.version, self.voice = "local-qwen3-tts", "unknown", "unknown"
        self.model: str | None = None
        self.sample_rate = 24000
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
                limit=1 << 22,
            )
        except OSError as error:
            raise SynthesizerUnavailable(f"合成进程无法启动：{type(error).__name__}") from error
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), self.start_timeout)
            ready = json.loads(line) if line else {"ready": False, "error": "exited"}
        except TimeoutError:
            ready = {"ready": False, "error": "timeout"}
        except ValueError:
            ready = {"ready": False, "error": "invalid_ready_line"}
        if not isinstance(ready, dict) or not ready.get("ready"):
            await self.close_process()
            ready = ready if isinstance(ready, dict) else {"error": "invalid_ready_line"}
            detail = f"（{ready['detail']}）" if ready.get("detail") else ""
            log.warning(
                "synthesizer not ready error=%s detail=%s", ready.get("error"), ready.get("detail")
            )
            raise SynthesizerUnavailable(f"合成进程未就绪：{ready.get('error')}{detail}")
        self.ready, self.exited = ready, False
        self.service, self.version = ready["synthesizer"], ready["version"]
        self.voice, self.sample_rate = ready["voice"], ready["sample_rate"]
        self.model = f"{ready['model']}@{ready['revision']}"
        log.info(
            "synthesizer ready device=%s load_ms=%s model=%s voice=%s",
            ready.get("device"),
            ready.get("load_ms"),
            self.model,
            self.voice,
        )
        return ready

    async def stream(self, text: str, *, seed: int | None = None):
        # ponytail: one sentence at a time; a dead process comes back in the background
        # (another ~7 s load) and the class speaks in text meanwhile.
        async with self.lock:
            if self.ready is None or self.exited or self.process is None:
                raise SynthesisFailed("not_ready")
            request_id = str(uuid4())
            stats = dict.fromkeys(
                ("first_chunk_ms", "elapsed_ms", "audio_seconds", "truncated", "memory_bytes")
            ) | {"chunks": 0, "cancel_ms": None}
            self.last_stats = stats
            started, finished, outcome = time.perf_counter(), False, "cancelled"
            request = {"id": request_id, "op": "synthesize", "text": text, "seed": seed}
            try:
                await self.send(request)
                while True:
                    reply = await self.reply(request_id)
                    if "audio" in reply:
                        try:
                            audio = base64.b64decode(reply["audio"], validate=True)
                        except TypeError, ValueError:
                            raise SynthesisFailed("failed") from None
                        if stats["first_chunk_ms"] is None:
                            stats["first_chunk_ms"] = elapsed_ms(started)
                        stats["chunks"] += 1
                        yield audio
                        continue
                    finished = True
                    stats["elapsed_ms"] = elapsed_ms(started)
                    if not reply.get("done"):
                        error = reply.get("error")
                        raise SynthesisFailed(error if error in REFUSALS else "failed")
                    stats |= {
                        key: reply.get(key)
                        for key in ("audio_seconds", "truncated", "memory_bytes")
                    }
                    if reply.get("truncated"):
                        raise SynthesisFailed("truncated")
                    outcome = "completed"
                    return
            except SynthesisFailed as error:
                outcome = f"failed:{error.reason}"
                raise
            finally:
                if not finished and not self.exited:
                    await self.cancel(request_id)
                log.info(
                    "synthesis outcome=%s chars=%d first_chunk_ms=%s elapsed_ms=%s chunks=%d "
                    "audio_seconds=%s cancel_ms=%s exited=%s",
                    outcome,
                    len(text),
                    stats["first_chunk_ms"],
                    stats["elapsed_ms"],
                    stats["chunks"],
                    stats["audio_seconds"],
                    stats["cancel_ms"],
                    self.exited,
                )

    async def send(self, message: dict):
        try:
            self.process.stdin.write(json.dumps(message).encode() + b"\n")
            await self.process.stdin.drain()
        except BrokenPipeError, ConnectionResetError:
            self.died()
            raise SynthesisFailed("failed") from None

    async def reply(self, request_id: str) -> dict:
        """The next line for this request; lines of earlier, cancelled requests are dropped."""
        while True:
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), self.chunk_timeout)
            except TimeoutError:
                raise SynthesisFailed("timeout") from None
            except ValueError:  # Line over the stream limit.
                raise SynthesisFailed("failed") from None
            if not line:
                self.died()
                raise SynthesisFailed("failed")
            try:
                reply = json.loads(line)
            except ValueError:
                raise SynthesisFailed("failed") from None
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return reply

    async def cancel(self, request_id: str):
        """Stop generation; without an acknowledgement in time the process is ended."""
        started = time.perf_counter()
        try:
            await self.send({"id": request_id, "op": "cancel"})
            async with asyncio.timeout(self.cancel_timeout):
                while True:
                    reply = await self.reply(request_id)
                    if reply.get("cancelled") or reply.get("done") or "error" in reply:
                        self.last_stats["cancel_ms"] = elapsed_ms(started)
                        return
        except TimeoutError, SynthesisFailed:
            await self.kill()

    async def kill(self):
        self.exited = True
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        self.died()

    def died(self):
        """Without this, speech stayed off until the service restarted (SELF-4, after a 47 s
        stall under memory pressure); one new process at a time, requests meanwhile fail fast."""
        self.exited = True
        if not self.closing and (self.reviving is None or self.reviving.done()):
            self.reviving = asyncio.create_task(self.revive())

    async def revive(self):
        started = time.perf_counter()
        await self.close_process()
        try:
            await self.start()
            outcome = "ready"
        except SynthesizerUnavailable:
            outcome = "failed"
        log.info("synthesizer revive outcome=%s ms=%d", outcome, elapsed_ms(started))

    def reasons(self) -> list[str]:
        reasons = file_reasons(self.root)
        if self.ready is None:
            reasons.append("合成进程未启动")
        elif self.reviving and not self.reviving.done():
            reasons.append("合成进程正在重新启动，请稍候")
        elif self.exited or self.process is None or self.process.returncode is not None:
            reasons.append("合成进程已退出")
        else:
            record = read_init(self.root) or {}
            if record.get("identity") and record["identity"] != identity(self.ready):
                reasons.append(
                    "合成器版本、声音配置或模型与探针记录不一致，"
                    "请重新运行 project-j-tts-init probe"
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
