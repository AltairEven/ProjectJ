"""Model files: digest verification, loading and chunked phone posteriors."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

import numpy as np

from project_j_scorer.rules import (
    CHUNK_SECONDS,
    CONTEXT_SECONDS,
    FRAME_SECONDS,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_REVISION,
    SAMPLE_RATE,
    SCORER,
    THRESHOLDS,
    VERSION,
)
from project_j_scorer.score import score_posteriors, unknown
from project_j_scorer.text import espeak, reference_phones

HOP = int(FRAME_SECONDS * SAMPLE_RATE)  # Samples per output frame (wav2vec2 conv stride 320).


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


def chunk_spans(
    n: int, rate: int = SAMPLE_RATE, chunk: float = CHUNK_SECONDS, context: float = CONTEXT_SECONDS
) -> list[tuple[int, int, int, int]]:
    """(window_start, window_end, keep_start, keep_end) sample indices covering [0, n) once."""
    size, pad = int(chunk * rate), int(context * rate)
    return [
        (max(0, start - pad), min(n, start + size + pad), start, min(n, start + size))
        for start in range(0, n, size)
    ]


class GopEngine:
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
        try:
            import torch
            from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

            self.torch = torch
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
            self.peak_bytes: int | None = None  # GPU memory after the last call's inference.
            self.extractor = Wav2Vec2FeatureExtractor.from_pretrained(models, local_files_only=True)
            model = Wav2Vec2ForCTC.from_pretrained(models, local_files_only=True)
            self.model = model.to(self.device).eval()
            self.vocab = json.loads((models / "vocab.json").read_text())
            self.phonemize = espeak()
            # Warm up at load, not mid-class: the first espeak call and MPS pass are slow (~1.3 s).
            self.phonemize(["Warm up."])
            self.posteriors(np.zeros(SAMPLE_RATE, dtype=np.float32))
            if self.device == "mps":
                torch.mps.empty_cache()
        except Exception as error:  # Kind only; library messages may carry local paths.
            raise ModelError("load_failed", type(error).__name__) from error
        self.identity = {
            "scorer": SCORER,
            "version": VERSION,
            "thresholds": THRESHOLDS,
            "model": MODEL_REPO,
            "revision": MODEL_REVISION,
            "device": self.device,
        }
        self.load_ms = round((time.perf_counter() - started) * 1000)

    def posteriors(self, audio: np.ndarray) -> np.ndarray:
        # ponytail: windows bound attention memory for long recordings; scores near a window
        # edge may shift slightly. Recordings up to 40 s are one window and unaffected.
        parts = []
        for start, end, keep_start, keep_end in chunk_spans(len(audio)):
            inputs = self.extractor(
                audio[start:end], sampling_rate=SAMPLE_RATE, return_tensors="pt"
            ).input_values.to(self.device)
            with self.torch.inference_mode():
                logits = self.model(inputs).logits[0].float()
            log_probs = self.torch.log_softmax(logits, dim=-1).cpu().numpy()
            if self.device == "mps":  # Read before the cache is released, so it is the peak.
                used = int(self.torch.mps.driver_allocated_memory())
                self.peak_bytes = max(self.peak_bytes or 0, used)
            first, last = (keep_start - start) // HOP, (keep_end - start) // HOP
            parts.append(log_probs[first : min(last, len(log_probs))])
        return np.concatenate(parts)

    def score(self, audio: np.ndarray, reference: str) -> dict:
        self.peak_bytes = None
        words = reference_phones(reference, self.phonemize)
        if not words:
            return unknown("failed")
        try:
            return score_posteriors(self.posteriors(audio), self.vocab, words)
        finally:
            if self.device == "mps":
                self.torch.mps.empty_cache()  # Leave GPU memory to the separate TTS process.

    def memory_bytes(self) -> int | None:
        return self.peak_bytes
