import pytest
from pydantic import BaseModel

from project_j.platform.models.contracts import (
    CapabilityReport,
    ModelRequest,
    ProbeItem,
    admission,
)


class Out(BaseModel):
    text: str


def report(**items):
    return CapabilityReport(
        adapter="x",
        adapter_version="1",
        model="m",
        simulated=False,
        captured_at="2026-10-14T00:00:00+00:00",
        items={name: ProbeItem(status=status) for name, status in items.items()},
    )


def test_request_rejects_identity_fields():
    with pytest.raises(ValueError, match="identity"):
        ModelRequest("classroom_judgment", "judge", {"name": "Ann"}, Out, deadline_seconds=10)


def test_identity_fields_rejected_at_any_depth():
    with pytest.raises(ValueError, match="identity"):
        ModelRequest("evaluation", "judge", {"items": [{"Age": 7}]}, Out, deadline_seconds=10)


def test_request_rejects_unknown_purpose_and_empty_instructions():
    with pytest.raises(ValueError):
        ModelRequest("chat", "judge", {}, Out, deadline_seconds=10)
    with pytest.raises(ValueError):
        ModelRequest("evaluation", "  ", {}, Out, deadline_seconds=10)


def test_a_fast_tier_binding_needs_its_tier_probed():
    base = {
        "auth": "verified",
        "model": "verified",
        "structured_output": "verified",
        "cancellation": "verified",
        "effort:low": "unverified",
    }
    assert admission(report(**base), "classroom_judgment", "low") == []
    reasons = admission(report(**base), "classroom_judgment", "low", "fast")
    assert reasons == ["Fast tier: not probed"]
    listed = report(**base, **{"tier:fast": "unverified"})
    assert admission(listed, "classroom_judgment", "low", "fast") == []


def test_unverified_items_pass_admission_but_failed_or_missing_items_do_not():
    ok = report(
        auth="verified",
        model="unverified",
        structured_output="verified",
        cancellation="verified",
        **{"effort:low": "unverified"},
    )
    assert admission(ok, "classroom_judgment", "low") == []
    bad = report(auth="verified", model="failed", structured_output="unsupported")
    reasons = admission(bad, "classroom_judgment", "low")
    assert any(r.startswith("Model") for r in reasons)
    assert any(r.startswith("Structured output") for r in reasons)
    assert any(r.startswith("Cancel") for r in reasons)  # never probed
    assert any(r.startswith("Effort low") for r in reasons)
    assert admission(None, "evaluation", "medium") == ["not probed yet"]


@pytest.mark.parametrize("purpose", ["planning", "preparation"])
def test_planning_and_preparation_need_the_evaluation_capabilities(purpose):
    ok = report(
        auth="verified",
        model="verified",
        structured_output="verified",
        **{"effort:medium": "verified"},
    )
    assert admission(ok, purpose, "medium") == []
    assert ModelRequest(purpose, "plan", {}, Out, deadline_seconds=10).purpose == purpose


def test_evaluation_does_not_require_cancellation():
    ok = report(
        auth="verified",
        model="verified",
        structured_output="verified",
        **{"effort:medium": "verified"},
    )
    assert admission(ok, "evaluation", "medium") == []


def test_parent_facing_reasons_use_english_names():
    bad = report(auth="verified", model="failed", structured_output="unsupported")
    reasons = admission(bad, "classroom_judgment", "low")
    assert "Model: failed" in reasons and "Structured output: not supported" in reasons
    assert "Effort low: not probed" in reasons
    assert not any("structured_output" in r or "effort:" in r for r in reasons)


def test_reading_purpose_is_known_and_needs_what_preparation_needs():
    from project_j.platform.models.contracts import PURPOSE_LABELS, PURPOSES, REQUIRED, ImageNote

    assert "reading" in PURPOSES and PURPOSE_LABELS["reading"] == "Expanded reading"
    assert REQUIRED["reading"] == REQUIRED["preparation"]
    ModelRequest("reading", "x", {}, ImageNote, deadline_key="preparation_drafting")


def test_reading_without_its_own_binding_uses_the_default(tmp_path):
    from project_j.platform.models.config import ModelFiles

    files = ModelFiles(tmp_path)
    files.write(
        "config.json",
        {
            "default": {"adapter": "simulated", "model": "m", "effort": "xhigh"},
            "purposes": {"preparation": {"adapter": "simulated", "model": "p", "effort": "low"}},
        },
    )
    assert files.bindings()["reading"].model == "m"
