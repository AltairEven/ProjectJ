from pathlib import Path

import pytest
from pydantic import ValidationError

from project_j.latency import MeasurementRun, RoundMeasurement, summarize

FIXTURE = Path(__file__).parent / "fixtures/latency_simulated.json"


def run_data():
    return MeasurementRun.model_validate_json(FIXTURE.read_text()).model_dump()


def test_failed_rounds_stay_in_percentiles():
    run = MeasurementRun.model_validate_json(FIXTURE.read_text())
    result = summarize(run)
    assert result["samples"] == 40
    assert result["failed_or_unknown"] == 2
    assert result["failure_fraction"] == 0.05
    assert result["p95_ms"] == 4500
    assert result["measurement_thresholds_met"]
    assert result["phase_ms"][0]["submit"] == 800
    assert run.rounds[0].effective_latency_ms == 2000  # From the end press/submit to playback.
    assert not result["actual_voice_verified"]
    assert result["qualification"] == "not_granted"
    assert result["evidence"] == "simulated"


def test_third_failed_or_filler_round_breaks_p95_and_is_never_dropped():
    data = run_data()
    data["rounds"][0]["outcome"] = "filler_only"
    data["rounds"][0]["failure_or_fallback_ms"] = 10000
    result = summarize(MeasurementRun.model_validate(data))
    assert result["samples"] == 40 and result["failed_or_unknown"] == 3
    assert result["p95_ms"] is None
    assert not result["measurement_thresholds_met"]


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_missing_or_repeated_samples_cannot_hide_a_slow_round(change):
    data = run_data()
    if change == "missing":
        data["rounds"] = data["rounds"][:-1]
    else:
        data["rounds"] = data["rounds"] + (data["rounds"][-1],)
    with pytest.raises(ValidationError):
        MeasurementRun.model_validate(data)


def test_unhandled_timeout_fails_even_if_95_percent_have_effective_responses():
    data = run_data()
    data["rounds"][-1]["failure_or_fallback_ms"] = None
    assert not summarize(MeasurementRun.model_validate(data))["measurement_thresholds_met"]


@pytest.mark.parametrize(
    "change",
    [
        {"playback_started_ms": None},
        {"input_ended_ms": float("nan")},
        {"judgment_done_ms": 100},
        {"scored_ms": 5000},
        {"tts_first_chunk_ms": 5000},
    ],
)
def test_no_backend_only_or_invalid_clock_measurement_can_claim_latency(change):
    data = run_data()["rounds"][0] | change
    with pytest.raises(ValidationError):
        RoundMeasurement.model_validate(data)


def test_a_presynthesized_line_needs_no_synthesis_phase():
    data = run_data()["rounds"][0] | {"tts_first_chunk_ms": None}
    assert RoundMeasurement.model_validate(data).effective_latency_ms == 2000


def test_failure_or_fallback_after_ten_seconds_does_not_satisfy_control_deadline():
    data = run_data()
    data["rounds"][-1]["failure_or_fallback_ms"] = 10001
    assert not summarize(MeasurementRun.model_validate(data))["timeout_control_ok"]
