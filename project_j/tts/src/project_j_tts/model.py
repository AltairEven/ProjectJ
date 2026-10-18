"""Model files: digest verification and the synthesis engine."""

import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import numpy as np

from project_j_tts.rules import (
    INSTRUCT,
    LANGUAGE,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_REVISION,
    REPETITION_PENALTY,
    SAMPLE_RATE,
    SPEAKER,
    STREAM_SECONDS,
    SYNTHESIZER,
    TEMPERATURE,
    TOP_K,
    TOP_P,
    VERSION,
    VOICE,
    max_tokens_for,
)


class ModelError(Exception):
    def __init__(
        self, kind: Literal["models_missing", "digest_mismatch", "load_failed"], detail=""
    ):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def verify(models: Path) -> dict:
    manifest_path = models / "manifest.json"
    if not manifest_path.is_file():
        raise ModelError("models_missing", str(manifest_path))
    manifest = json.loads(manifest_path.read_text())
    for name, info in manifest["files"].items():
        path = models / name
        if not path.is_file():
            raise ModelError("models_missing", name)
        if path.stat().st_size != info["bytes"] or sha256_file(path) != info["sha256"]:
            raise ModelError("digest_mismatch", name)
    return manifest


class QwenEngine:
    sample_rate = SAMPLE_RATE

    def __init__(self, models: Path):
        started = time.perf_counter()
        manifest = verify(models)
        pinned = {name: info["sha256"] for name, info in manifest["files"].items()}
        if (manifest.get("repo"), manifest.get("revision"), pinned) != (
            MODEL_REPO,
            MODEL_REVISION,
            MODEL_FILES,
        ):
            raise ModelError("digest_mismatch", "manifest differs from the pinned model")
        self.truncated = False
        try:
            import mlx.core as mx
            from mlx_audio.tts.utils import load_model

            self.mx = mx
            self.model = load_model(models)
            if self.model.sample_rate != SAMPLE_RATE:
                raise ValueError("unexpected sample rate")
            # Warm up at load, not mid-class: the first generation compiles GPU kernels.
            for _ in self.stream("你好。", None):
                pass
            device = "gpu" if mx.default_device().type == mx.gpu else "cpu"
        except Exception as error:  # Kind only; library messages may carry local paths.
            raise ModelError("load_failed", type(error).__name__) from error
        self.identity = {
            "synthesizer": SYNTHESIZER,
            "version": VERSION,
            "voice": VOICE,
            "model": MODEL_REPO,
            "revision": MODEL_REVISION,
            "device": device,
        }
        self.load_ms = round((time.perf_counter() - started) * 1000)

    def stream(self, text: str, seed: int | None) -> Iterator[np.ndarray]:
        mx = self.mx
        self.truncated = False
        if seed is not None:
            mx.random.seed(seed)
        mx.reset_peak_memory()
        limit, tokens = max_tokens_for(text), 0
        results = self.model.generate(
            text=text,
            voice=SPEAKER,
            instruct=INSTRUCT,
            lang_code=LANGUAGE,
            temperature=TEMPERATURE,
            top_k=TOP_K,
            top_p=TOP_P,
            repetition_penalty=REPETITION_PENALTY,
            max_tokens=limit,
            stream=True,
            streaming_interval=STREAM_SECONDS,
        )
        try:
            for result in results:
                tokens += result.token_count
                yield np.array(result.audio.astype(mx.float32))
            self.truncated = tokens >= limit
        finally:
            results.close()
            mx.clear_cache()  # Also when cancelled: leave GPU memory to the scorer process.

    def memory_bytes(self) -> int:
        return int(self.mx.get_peak_memory())
