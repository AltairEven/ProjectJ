"""Resident scoring process: one JSON object per line on stdin and stdout (ADR-0015).

The recording arrives base64-encoded in memory and is never written to disk.
"""

import base64
import io
import json
import os
import sys
import time
import wave
from collections.abc import Callable
from typing import BinaryIO, Protocol

import numpy as np

from project_j_scorer.model import ModelError
from project_j_scorer.rules import MAX_AUDIO_SECONDS, SAMPLE_RATE

FAILED = {"status": "unknown", "reason": "failed"}


class AudioError(ValueError):
    pass


class Engine(Protocol):
    identity: dict
    load_ms: int

    def score(self, audio: np.ndarray, reference: str) -> dict: ...

    def memory_bytes(self) -> int | None: ...


def decode_wav(data: bytes) -> np.ndarray:
    """16 kHz mono 16-bit PCM WAV within the limit, as float32 in [-1, 1]."""
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            shape = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if shape != (1, 2, SAMPLE_RATE) or source.getcomptype() != "NONE":
                raise AudioError("expected 16 kHz mono 16-bit PCM WAV")
            if source.getnframes() > MAX_AUDIO_SECONDS * SAMPLE_RATE:
                raise AudioError("recording too long")
            frames = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as error:
        raise AudioError("not a WAV recording") from error
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768


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
            "load_ms": engine.load_ms,
            "memory_bytes": engine.memory_bytes(),
        },
    )
    for line in stdin:
        request_id = None
        started = time.perf_counter()
        try:
            request = json.loads(line)
            request_id = request.get("id")
            audio = decode_wav(base64.b64decode(request["audio"], validate=True))
            result = engine.score(audio, request["reference"])
        except Exception as error:  # One bad request never stops the process; kind only.
            print(f"request failed: {type(error).__name__}", file=sys.stderr)
            send(stdout, {"id": request_id, **FAILED})
            continue
        elapsed = round((time.perf_counter() - started) * 1000)
        send(
            stdout,
            {
                "id": request_id,
                **result,
                "elapsed_ms": elapsed,
                "memory_bytes": engine.memory_bytes(),
            },
        )
    return 0
