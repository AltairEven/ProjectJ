"""One resident child process of a separate local project (scorer ADR-0015, TTS ADR-0016).

JSON lines over stdin/stdout, a ready line first, a new process in the background when it dies.
The two projects stay separate; only this side of the pipe is shared.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from project_j.platform.files import private_write

log = logging.getLogger(__name__)


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class LocalProcess:
    kind = ""  # Log name: scorer, synthesizer.
    noun = ""  # Scorer, Speech: what the student and the logs read.
    cli_noun = ""  # 评分, 合成: what the terminal commands print.
    init_command = ""  # project-j-scorer-init, project-j-tts-init.
    models = init = binary = Path()
    identity_keys: tuple[str, ...] = ()
    identity_words = ""  # What must match the probe record, in words.
    stream_limit = 1 << 20
    Unavailable: type[Exception] = Exception
    simulated = False

    def __init__(
        self,
        root: Path,
        command: Sequence[str] | None = None,
        *,
        start_timeout: float,
    ):
        self.root = root
        self.command = (
            list(command)
            if command
            else [str(root / self.binary), "serve", "--models", str(root / self.models)]
        )
        self.start_timeout = start_timeout
        self.model: str | None = None
        self.ready: dict | None = None
        self.last_stats: dict = {}
        self.process: asyncio.subprocess.Process | None = None
        self.exited = False
        self.reviving: asyncio.Task | None = None
        self.closing = False  # Service shutdown: a process that ends now is not replaced.
        self.lock = asyncio.Lock()

    @classmethod
    def identity(cls, ready: dict) -> dict:
        return {key: ready.get(key) for key in cls.identity_keys}

    @classmethod
    def read_init(cls, root: Path) -> dict | None:
        path = root / cls.init
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except OSError, ValueError:
            return {"passed": False, "reasons": ["the setup record cannot be read"]}

    @classmethod
    def file_reasons(cls, root: Path) -> list[str]:
        manifest = root / cls.models / "manifest.json"
        if not manifest.is_file():
            return [f"{cls.noun} model not downloaded; run {cls.init_command} download"]
        record = cls.read_init(root)
        if record is None:
            return [f"{cls.noun} not probed yet; run {cls.init_command} probe"]
        if not record.get("passed"):
            return [
                f"{cls.noun} probe failed: "
                + "; ".join(record.get("reasons") or ["no reason recorded"])
            ]
        if record.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
            return [f"{cls.noun} model changed; run {cls.init_command} probe again"]
        return []

    @classmethod
    def save_init(cls, root: Path, record: dict):
        private_write(
            root / cls.init, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
        )

    def adopt(self, ready: dict) -> tuple[str, object]:
        """Take the ready line's identity; returns the one extra field the ready log shows."""
        raise NotImplementedError

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
                stderr=None,  # Child errors land in the service log.
                env=env,
                limit=self.stream_limit,
                # Own group: Ctrl+C or closing the terminal reaches only the server, which then
                # closes this child; if the server dies, the child exits on end of input.
                start_new_session=True,
            )
        except OSError as error:
            raise self.Unavailable(
                f"{self.noun} process cannot start: {type(error).__name__}"
            ) from error
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
            detail = f" ({ready['detail']})" if ready.get("detail") else ""
            log.warning(
                "%s not ready error=%s detail=%s",
                self.kind,
                ready.get("error"),
                ready.get("detail"),
            )
            raise self.Unavailable(f"{self.noun} process not ready: {ready.get('error')}{detail}")
        self.ready, self.exited = ready, False
        self.model = f"{ready['model']}@{ready['revision']}"
        name, value = self.adopt(ready)
        log.info(
            "%s ready device=%s load_ms=%s model=%s %s=%s",
            self.kind,
            ready.get("device"),
            ready.get("load_ms"),
            self.model,
            name,
            value,
        )
        return ready

    def died(self):
        """Without this the process stayed off until the service restarted (SELF-4, after a 47 s
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
        except self.Unavailable:
            outcome = "failed"
        log.info("%s revive outcome=%s ms=%d", self.kind, outcome, elapsed_ms(started))

    def reasons(self) -> list[str]:
        reasons = self.file_reasons(self.root)
        if self.ready is None:
            reasons.append(f"{self.noun} process not started")
        elif self.reviving and not self.reviving.done():
            reasons.append(f"{self.noun} process is restarting; please wait")
        elif self.exited or self.process is None or self.process.returncode is not None:
            reasons.append(f"{self.noun} process has stopped")
        else:
            record = self.read_init(self.root) or {}
            if record.get("identity") and record["identity"] != self.identity(self.ready):
                reasons.append(
                    f"{self.noun}: the {self.identity_words} no longer matches the probe record; "
                    f"run {self.init_command} probe again"
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


def init_main(
    local: type[LocalProcess],
    description: str,
    subproject: str,
    probe: Callable,
    show: Callable[[dict], None],
    adult: tuple[str, str, Callable[[argparse.ArgumentParser], None], Callable],
    argv: list[str] | None,
    command: Sequence[str] | None,
) -> int:
    """project-j-scorer-init and project-j-tts-init: download, probe, status and one adult
    check (adult: name, help, add its arguments, run(root, command, args) -> exit code)."""
    parser = argparse.ArgumentParser(description=description)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("download").add_argument("--cache-dir", type=Path)
    commands.add_parser("probe")
    commands.add_parser("status")
    name, help_text, arguments, run = adult
    arguments(commands.add_parser(name, help=help_text))
    args = parser.parse_args(argv)
    if not Path("project_j").is_dir():
        print("请在项目根目录运行（当前目录下没有 project_j）。")
        return 1
    root = Path.cwd()
    if args.command == "download":
        binary = root / local.binary
        if not binary.exists():
            print(
                f"{local.cli_noun}子工程未安装，请先运行 "
                f"project_j/scripts/uv.sh sync --locked --project project_j/{subproject}"
            )
            return 1
        call = [str(binary), "download", "--models", str(root / local.models)]
        if args.cache_dir:
            call += ["--cache-dir", str(args.cache_dir.resolve())]
        env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        code = subprocess.run(call, cwd=root, env=env, check=False).returncode
        if code == 0:
            print(f"请运行 {local.init_command} probe。")
        return code
    if args.command == "probe":
        record = asyncio.run(probe(root, local(root, command)))
        print(
            f"{local.cli_noun}探针通过。" if record["passed"] else f"{local.cli_noun}探针未通过。"
        )
        show(record)
        return 0 if record["passed"] else 1
    if args.command == "status":
        if reasons := local.file_reasons(root):
            print(f"{local.cli_noun}器未就绪。")
            for reason in reasons:
                print(f"- {reason}")
            return 1
        print(f"{local.cli_noun}器已就绪。")
        show(local.read_init(root))
        return 0
    return asyncio.run(run(root, command, args))
