"""Analyze post-run measurements. Never captures audio or estimates actual playback."""

import argparse
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import median
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from project_j.contracts import Contract


class RoundMeasurement(Contract):
    sample_id: str = Field(min_length=1)
    # All on one browser monotonic clock (ms). Start: a typed submit, or the reading's end press.
    input_ended_ms: float = Field(ge=0, allow_inf_nan=False)
    submitted_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    scored_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)  # Reading only.
    judgment_done_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # None when a presynthesized line plays directly.
    tts_first_chunk_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    playback_started_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    outcome: Literal["effective", "failure", "timeout", "unknown", "filler_only"]
    failure_or_fallback_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_order(self) -> Self:
        phases = (
            self.submitted_ms,
            self.scored_ms,
            self.judgment_done_ms,
            self.tts_first_chunk_ms,
            self.playback_started_ms,
        )
        for stamp in (*phases, self.failure_or_fallback_ms):
            if stamp is not None and stamp < self.input_ended_ms:
                raise ValueError("timestamp precedes the end of input")
        if self.outcome == "effective" and None in (
            self.submitted_ms,
            self.judgment_done_ms,
            self.playback_started_ms,
        ):
            raise ValueError("effective round needs submit, judgment and actual playback")
        sequence = [x for x in phases if x is not None]
        if sequence != sorted(sequence):
            raise ValueError("submit, scoring, judgment, synthesis and playback order invalid")
        return self

    @property
    def effective_latency_ms(self) -> float:
        if self.outcome != "effective" or self.playback_started_ms is None:
            return float("inf")  # Failed/unknown rounds remain in the denominator.
        return self.playback_started_ms - self.input_ended_ms


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
    pronunciation_service: str | None = None
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
    # Nearest-rank P95 keeps failures (inf) in the denominator and slowest tail.
    p50, p95 = median(values), values[ceil(0.95 * len(values)) - 1]
    failed = sum(r.outcome != "effective" for r in run.rounds)
    timeout_control_ok = all(
        r.failure_or_fallback_ms is not None
        and r.failure_or_fallback_ms - r.input_ended_ms <= 10000
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
                "submit": _elapsed(r.submitted_ms, r.input_ended_ms),
                "scoring": _elapsed(r.scored_ms, r.submitted_ms),
                "judgment": _elapsed(r.judgment_done_ms, r.scored_ms or r.submitted_ms),
                "synthesis_first_chunk": _elapsed(r.tts_first_chunk_ms, r.judgment_done_ms),
                "actual_playback": _elapsed(
                    r.playback_started_ms, r.tts_first_chunk_ms or r.judgment_done_ms
                ),
            }
            for r in run.rounds
        ],
    }


def _elapsed(end: float | None, start: float | None) -> float | None:
    return None if end is None or start is None else end - start


def main() -> int:
    """Post-run analysis only; does not collect or write runtime data."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("measurements", type=Path)
    args = parser.parse_args()
    run = MeasurementRun.model_validate_json(args.measurements.read_text())
    print(json.dumps(summarize(run), ensure_ascii=False, indent=2, allow_nan=False))
    return 0
