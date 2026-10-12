"""Analyze post-run measurements. Never captures audio or estimates actual playback."""

from math import ceil, isfinite
from statistics import median
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from project_j.contracts import Contract


class RoundMeasurement(Contract):
    sample_id: str = Field(min_length=1)
    # All timestamps projected onto the same browser monotonic clock, in milliseconds.
    candidate_end_ms: float = Field(ge=0, allow_inf_nan=False)
    window_expired_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    asr_final_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    submitted_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    judgment_done_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tts_first_chunk_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    playback_started_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    outcome: Literal["effective", "failure", "timeout", "unknown", "filler_only"]
    failure_or_fallback_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_order(self) -> Self:
        for stamp in (
            self.window_expired_ms,
            self.asr_final_ms,
            self.submitted_ms,
            self.judgment_done_ms,
            self.tts_first_chunk_ms,
            self.playback_started_ms,
            self.failure_or_fallback_ms,
        ):
            if stamp is not None and stamp < self.candidate_end_ms:
                raise ValueError("timestamp precedes VAD candidate end")
        if self.outcome == "effective":
            required = (
                self.window_expired_ms,
                self.asr_final_ms,
                self.submitted_ms,
                self.judgment_done_ms,
                self.tts_first_chunk_ms,
                self.playback_started_ms,
            )
            if any(x is None for x in required):
                raise ValueError("effective round needs every phase, including actual playback")
        if self.submitted_ms is not None:
            for stamp in (self.window_expired_ms, self.asr_final_ms):
                if stamp is not None and stamp > self.submitted_ms:
                    raise ValueError("submission before completion conditions")
        sequence = [
            x
            for x in (
                self.submitted_ms,
                self.judgment_done_ms,
                self.tts_first_chunk_ms,
                self.playback_started_ms,
            )
            if x is not None
        ]
        if sequence != sorted(sequence):
            raise ValueError("judgment, synthesis and playback order invalid")
        return self

    @property
    def effective_latency_ms(self) -> float:
        if self.outcome != "effective" or self.playback_started_ms is None:
            return float("inf")  # Failed/unknown rounds remain in the denominator.
        return self.playback_started_ms - self.candidate_end_ms


class MeasurementRun(Contract):
    schema_version: Literal[1] = 1
    mode: Literal["simulated", "adult_actual"]
    captured_at: AwareDatetime
    configuration_ref: str = Field(min_length=1)
    input_policy_ref: str = Field(min_length=1)
    browser: str = Field(min_length=1)
    os: str = Field(min_length=1)
    input_device: str = Field(min_length=1)
    output_device: str = Field(min_length=1)
    network: str = Field(min_length=1)
    model: Literal["gpt-6.1-sol"]
    effort: Literal["low"]
    service_tier: str = Field(min_length=1)
    transcription_model: str = Field(min_length=1)
    tts_model: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    clock_id: str = Field(min_length=1)
    expected_sample_ids: tuple[str, ...]
    rounds: tuple[RoundMeasurement, ...]

    @model_validator(mode="after")
    def complete_samples(self) -> Self:
        expected, actual = self.expected_sample_ids, [r.sample_id for r in self.rounds]
        if not expected or len(set(expected)) != len(expected):
            raise ValueError("expected sample identities must be nonempty and unique")
        if len(set(actual)) != len(actual) or set(actual) != set(expected):
            raise ValueError("missing/duplicated/unplanned sample; never drop slow rounds")
        return self


def summarize(run: MeasurementRun) -> dict:
    values = sorted(r.effective_latency_ms for r in run.rounds)
    p50, p95 = median(values), values[ceil(0.95 * len(values)) - 1]
    failed = sum(r.outcome != "effective" for r in run.rounds)
    timeout_control_ok = all(
        r.failure_or_fallback_ms is not None
        and r.failure_or_fallback_ms - r.candidate_end_ms <= 10000
        for r in run.rounds
        if r.outcome == "timeout" or r.effective_latency_ms >= 10000
    )
    return {
        "evidence": run.mode,
        "qualification": "not_granted",  # Numbers cannot grant application/child eligibility.
        "samples": len(values),
        "failed_or_unknown": failed,
        "failure_fraction": failed / len(values),
        "median_ms": p50 if isfinite(p50) else None,
        "p95_ms": p95 if isfinite(p95) else None,
        "missing_percentile_means": "no effective response in that percentile",
        "within_5s_fraction": sum(v <= 5000 for v in values) / len(values),
        "timeout_control_ok": timeout_control_ok,
        "measurement_thresholds_met": (
            len(values) >= 40 and p50 <= 2500 and p95 <= 5000 and timeout_control_ok
        ),
        "actual_voice_verified": False,  # Requires the independent physical run review.
        "phase_ms": [
            {
                "sample_id": r.sample_id,
                "pause_window": _elapsed(r.window_expired_ms, r.candidate_end_ms),
                "final_transcript_from_vad": _elapsed(r.asr_final_ms, r.candidate_end_ms),
                "submit_after_ready": _elapsed(r.submitted_ms, _ready(r)),
                "judgment": _elapsed(r.judgment_done_ms, r.submitted_ms),
                "synthesis_first_chunk": _elapsed(r.tts_first_chunk_ms, r.judgment_done_ms),
                "actual_playback": _elapsed(r.playback_started_ms, r.tts_first_chunk_ms),
            }
            for r in run.rounds
        ],
    }


def _ready(r: RoundMeasurement) -> float | None:
    if r.window_expired_ms is None or r.asr_final_ms is None:
        return None
    return max(r.window_expired_ms, r.asr_final_ms)


def _elapsed(end: float | None, start: float | None) -> float | None:
    return None if end is None or start is None else end - start
