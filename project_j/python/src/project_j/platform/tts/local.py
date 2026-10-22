"""Local speech synthesis (ADR-0016): one resident process of the separate TTS project.

Text and audio travel through the process's stdin/stdout in memory; nothing is written to disk.
"""

import asyncio
import base64
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from project_j.platform.local_process import LocalProcess, elapsed_ms
from project_j.platform.tts.contracts import SynthesisFailed

log = logging.getLogger(__name__)
MODELS = Path("project_j/private/tts/models")
INIT = Path("project_j/private/tts/init.json")
BINARY = Path("project_j/tts/.venv/bin/project-j-tts")
REFUSALS = ("empty_text", "too_long")


class SynthesizerUnavailable(Exception):
    pass


class LocalSynthesizer(LocalProcess):
    kind, noun, init_command = "synthesizer", "Speech", "project-j-tts-init"
    cli_noun = "合成"
    models, init, binary = MODELS, INIT, BINARY
    identity_keys = ("synthesizer", "version", "voice", "model", "revision")
    identity_words = "version, voice setup or model"
    Unavailable = SynthesizerUnavailable
    stream_limit = 1 << 22

    def __init__(
        self,
        root: Path,
        command: Sequence[str] | None = None,
        *,
        start_timeout: float = 180.0,
        chunk_timeout: float = 30.0,
        cancel_timeout: float = 3.0,
        inherit_stderr: bool = True,
    ):
        super().__init__(root, command, start_timeout=start_timeout, inherit_stderr=inherit_stderr)
        self.chunk_timeout, self.cancel_timeout = chunk_timeout, cancel_timeout
        self.service, self.version, self.voice = "local-qwen3-tts", "unknown", "unknown"
        self.sample_rate = 24000

    def adopt(self, ready: dict) -> tuple[str, object]:
        self.service, self.version = ready["synthesizer"], ready["version"]
        self.voice, self.sample_rate = ready["voice"], ready["sample_rate"]
        return "voice", self.voice

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
