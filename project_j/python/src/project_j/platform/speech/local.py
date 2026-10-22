"""Local pronunciation scorer (ADR-0015): one resident process of the separate scorer project.

The recording goes through the process's stdin in memory; nothing is written to disk.
"""

import asyncio
import base64
import io
import json
import logging
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from project_j.platform.local_process import LocalProcess
from project_j.platform.speech.contracts import PRODUCT_LIMITS, PronunciationResult, WordScore

log = logging.getLogger(__name__)
MODELS = Path("project_j/private/scorer/models")
INIT = Path("project_j/private/scorer/init.json")
BINARY = Path("project_j/scorer/.venv/bin/project-j-scorer")
LIMITATIONS = ("Local scorer; the limits are not calibrated",)


def silence(seconds: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(32000 * seconds))
    return buffer.getvalue()


class ScorerUnavailable(Exception):
    pass


class LocalScorer(LocalProcess):
    kind, noun, init_command = "scorer", "Scorer", "project-j-scorer-init"
    cli_noun = "评分"
    models, init, binary = MODELS, INIT, BINARY
    identity_keys = ("scorer", "version", "thresholds", "model", "revision")
    identity_words = "version, model or limits"
    Unavailable = ScorerUnavailable
    limits = PRODUCT_LIMITS
    limitations = LIMITATIONS

    def __init__(
        self,
        root: Path,
        command: Sequence[str] | None = None,
        *,
        start_timeout: float = 120.0,
        score_timeout: float = 30.0,
        inherit_stderr: bool = True,
    ):
        super().__init__(root, command, start_timeout=start_timeout, inherit_stderr=inherit_stderr)
        self.score_timeout = score_timeout
        self.service, self.version = "local-gop", "unknown"
        self.thresholds: str | None = None

    def adopt(self, ready: dict) -> tuple[str, object]:
        self.service, self.version = ready["scorer"], ready["version"]
        self.thresholds = ready["thresholds"]
        return "thresholds", self.thresholds

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
