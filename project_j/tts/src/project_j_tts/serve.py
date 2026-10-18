"""Resident synthesis process: one JSON object per line on stdin and stdout (ADR-0016).

Text and audio stay in memory. A reader thread queues stdin so a cancel can arrive while a
sentence is being generated; generation stops at the next chunk boundary.
"""

import base64
import json
import os
import queue
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator
from typing import BinaryIO, Protocol

import numpy as np

from project_j_tts.model import ModelError
from project_j_tts.rules import MAX_TEXT_CHARS


class Engine(Protocol):
    identity: dict
    load_ms: int
    sample_rate: int
    truncated: bool  # Whether the last stream hit its generation cap.

    def stream(self, text: str, seed: int | None) -> Iterator[np.ndarray]: ...

    def memory_bytes(self) -> int | None: ...


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).round().astype("<i2").tobytes()


def protocol_stdout() -> BinaryIO:
    """Keep the real stdout for protocol lines; send every other print to stderr."""
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return protocol


def send(stdout: BinaryIO, message: dict):
    stdout.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
    stdout.flush()


def read_lines(stdin: BinaryIO, lines: queue.Queue):
    for line in stdin:
        if line.strip():
            lines.put(line)
    lines.put(None)  # End of input, handled in arrival order.


def is_cancel(line: bytes, request_id) -> bool:
    try:
        message = json.loads(line)
    except ValueError:
        return False
    return (
        isinstance(message, dict)
        and message.get("op") == "cancel"
        and message.get("id") == request_id
    )


def cancelled(request_id, lines: queue.Queue, pending: deque) -> bool:
    """Take what has arrived without waiting; keep everything but this request's cancel."""
    found = False
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            return found
        if not found and line is not None and is_cancel(line, request_id):
            found = True
        else:
            pending.append(line)


def synthesize(engine: Engine, request_id, text, seed, lines, pending, stdout):
    chunks = samples = 0
    stream = engine.stream(text, seed)
    try:
        for audio in stream:
            pcm = base64.b64encode(to_pcm16(audio)).decode()
            send(stdout, {"id": request_id, "chunk": chunks, "audio": pcm})
            chunks, samples = chunks + 1, samples + len(audio)
            if cancelled(request_id, lines, pending):
                send(stdout, {"id": request_id, "cancelled": True, "chunks": chunks})
                return
    finally:
        stream.close()
    send(
        stdout,
        {
            "id": request_id,
            "done": True,
            "chunks": chunks,
            "audio_seconds": round(samples / engine.sample_rate, 2),
            "truncated": engine.truncated,
            "memory_bytes": engine.memory_bytes(),
        },
    )


def run(factory: Callable[[], Engine], stdin: BinaryIO, stdout: BinaryIO) -> int:
    try:
        engine = factory()
    except ModelError as error:
        send(stdout, {"ready": False, "error": error.kind, "detail": error.detail})
        return 1
    send(
        stdout,
        {
            "ready": True,
            **engine.identity,
            "sample_rate": engine.sample_rate,
            "load_ms": engine.load_ms,
            "memory_bytes": engine.memory_bytes(),
        },
    )
    lines: queue.Queue[bytes | None] = queue.Queue()
    threading.Thread(target=read_lines, args=(stdin, lines), daemon=True).start()
    pending: deque[bytes | None] = deque()
    while True:
        line = pending.popleft() if pending else lines.get()
        if line is None:
            return 0
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            if request.get("op") == "cancel":  # Nothing of that id is being generated.
                send(stdout, {"id": request_id, "cancelled": True, "chunks": None})
                continue
            text, seed = request["text"], request.get("seed")
            if not isinstance(text, str) or not (seed is None or type(seed) is int):
                raise ValueError("bad synthesis request")
            if not text.strip():
                send(stdout, {"id": request_id, "error": "empty_text"})
            elif len(text) > MAX_TEXT_CHARS:
                send(stdout, {"id": request_id, "error": "too_long"})
            else:
                synthesize(engine, request_id, text, seed, lines, pending, stdout)
        except Exception as error:  # One bad request never stops the process; kind only.
            print(f"request failed: {type(error).__name__}", file=sys.stderr)
            send(stdout, {"id": request_id, "error": "failed"})
