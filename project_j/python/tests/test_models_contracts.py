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
        ModelRequest("classroom_judgment", "judge", {"name": "Ann"}, Out)


def test_identity_fields_rejected_at_any_depth():
    with pytest.raises(ValueError, match="identity"):
        ModelRequest("evaluation", "judge", {"items": [{"Age": 7}]}, Out)


def test_request_rejects_unknown_purpose_and_empty_instructions():
    with pytest.raises(ValueError):
        ModelRequest("chat", "judge", {}, Out)
    with pytest.raises(ValueError):
        ModelRequest("evaluation", "  ", {}, Out)


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
    assert any(r.startswith("模型") for r in reasons)
    assert any(r.startswith("结构化输出") for r in reasons)
    assert any(r.startswith("取消") for r in reasons)  # never probed
    assert any(r.startswith("档位low") for r in reasons)
    assert admission(None, "evaluation", "medium") == ["尚未探测"]


@pytest.mark.parametrize("purpose", ["planning", "preparation"])
def test_planning_and_preparation_need_the_evaluation_capabilities(purpose):
    ok = report(
        auth="verified",
        model="verified",
        structured_output="verified",
        **{"effort:medium": "verified"},
    )
    assert admission(ok, purpose, "medium") == []
    assert ModelRequest(purpose, "plan", {}, Out).purpose == purpose


def test_evaluation_does_not_require_cancellation():
    ok = report(
        auth="verified",
        model="verified",
        structured_output="verified",
        **{"effort:medium": "verified"},
    )
    assert admission(ok, "evaluation", "medium") == []


def test_parent_facing_reasons_use_chinese_names():
    bad = report(auth="verified", model="failed", structured_output="unsupported")
    reasons = admission(bad, "classroom_judgment", "low")
    assert "模型：失败" in reasons and "结构化输出：不支持" in reasons
    assert "档位low：未探测" in reasons
    assert not any("structured_output" in r or "effort:" in r for r in reasons)
