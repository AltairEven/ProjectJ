"""Local pronunciation scorer (ADR-0015): one resident process of the separate scorer project.

The recording goes through the process's stdin in memory; nothing is written to disk.
"""

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from project_j.platform.speech.contracts import PRODUCT_LIMITS, PronunciationResult, WordScore

MODELS = Path("project_j/private/scorer/models")
INIT = Path("project_j/private/scorer/init.json")
BINARY = Path("project_j/scorer/.venv/bin/project-j-scorer")
LIMITATIONS = ("本机评分器，门槛未校准",)
IDENTITY_KEYS = ("scorer", "version", "thresholds", "model", "revision")


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
        inherit_stderr: bool = False,
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
            await self.aclose()
            detail = f"（{ready['detail']}）" if ready.get("detail") else ""
            raise ScorerUnavailable(f"评分进程未就绪：{ready.get('error')}{detail}")
        self.ready, self.exited = ready, False
        self.service, self.version = ready["scorer"], ready["version"]
        self.thresholds = ready["thresholds"]
        self.model = f"{ready['model']}@{ready['revision']}"
        return ready

    async def score(self, audio: bytes, reference_text: str) -> PronunciationResult:
        # ponytail: a dead process is not restarted mid-class (another ~6 s load, and ADR-0015
        # keeps the model fixed during class); the next class start launches a new one.
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
                            self.exited = True
                            return PronunciationResult.unknown("failed", self)
                        reply = json.loads(line)
                        if reply.get("id") == request_id:  # Late replies of cancelled requests.
                            break
            except BrokenPipeError, ConnectionResetError:
                self.exited = True
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

    def reasons(self) -> list[str]:
        reasons = file_reasons(self.root)
        if self.ready is None:
            reasons.append("评分进程未启动")
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
