import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_j.contracts import ObjectEnvelope, SubmittedInput

FIXTURES = Path(__file__).parent / "fixtures"


def objects():
    return json.loads((FIXTURES / "e2e01_refs.json").read_text())


def submitted(**changes):
    data = dict(
        event_id="I1",
        activity_id="A1",
        learner_id="fictional-L1",
        task_instance_id="Q1",
        wait_id="W1",
        expected_core_revision=2,
        cancellation_epoch=0,
        client_sequence=1,
        input_policy_ref={"object_id": "IS1", "revision": 1},
        realm="simulation",
        source_type="simulated",
        modality="text",
        text="a bridge",
        provider="simulated speaker",
        intent="answer",
        completion_trigger="explicit",
    )
    return SubmittedInput.model_validate(data | changes)


def recording(**changes):
    return submitted(
        **{
            "modality": "audio",
            "text": None,
            "provider": "simulated_reader",
            "input_started_ms": 0.0,
            "input_ended_ms": 4200.0,
            "recording_ms": 4000.0,
            "pause_count": 1,
        }
        | changes
    )


@pytest.mark.parametrize(
    "change",
    [
        {"text": None},
        {"completion_trigger": "max_duration"},
        {"recording_ms": 10.0},
        {"modality": "transcript"},
        {"realm": "family"},
        {"relation": "supplement"},
        {"relation": "recognition_revision", "related_event_id": "I0"},
    ],
)
def test_text_submission_has_no_recording_fields_or_speech_recognition(change):
    with pytest.raises(ValidationError):
        submitted(**change)


@pytest.mark.parametrize(
    "change",
    [
        {"text": "a bridge"},
        {"recording_ms": None},
        {"input_ended_ms": None},
        {"input_started_ms": 5000.0},
        {"relation": "supplement", "related_event_id": "I0"},
    ],
)
def test_recording_needs_timing_no_text_and_is_never_a_supplement(change):
    with pytest.raises(ValidationError):
        recording(**change)


def test_text_and_recordings_are_explicit_submissions():
    assert submitted().modality == "text"
    assert submitted(relation="supplement", related_event_id="I0", event_id="I2").text
    assert recording(completion_trigger="max_duration").recording_ms == 4000.0
    assert recording().text is None  # Audio bytes never travel inside the contract.


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "interface"},
        {"learner_id": None},
        {"source_type": "child"},
        {"realm": "family"},
        {"revision": "latest"},
        {"unknown_field": True},
        {"created_at": "2026-10-12T12:00:00"},
    ],
)
def test_reject_identity_scope_revision_and_unknown_field(change):
    with pytest.raises(ValidationError):
        ObjectEnvelope.model_validate(objects()[0] | change)


def test_reference_answers_are_fixed_simulated_data_and_have_independent_sources():
    samples = json.loads((FIXTURES / "reference_answers.json").read_text())
    assert len({s["id"] for s in samples}) == 10
    assert {s["expected"] for s in samples} == {"correct", "incorrect", "unclear", "help"}
    assert all(s["source_type"] == "simulated" for s in samples)
    text = (FIXTURES.parents[3] / "courses/paper-bridge/reading-main.md").read_text()
    for sample in samples:
        if sample["expected"] == "correct":
            assert sample["reference"] in text
