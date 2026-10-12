import json
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from openai import OpenAI
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select

from project_j.contracts import SubmittedInput
from project_j.probe import live_probe, preflight, run_model_probe


@pytest.mark.parametrize(
    "effort,actual_effort,config_ok",
    [
        ("low", "low", True),
        ("medium", "medium", True),
        ("low", "medium", False),
        ("low", None, False),
    ],
)
def test_sdk_serializes_adopted_config_and_parses_structured_output_using_mock(
    effort, actual_effort, config_ok
):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "gpt-6.1-sol"
        assert payload["reasoning"]["effort"] == effort
        assert payload["service_tier"] == "default" and payload["store"] is False
        assert payload["text"]["format"]["type"] == "json_schema"
        return httpx2.Response(
            200,
            headers={"x-request-id": "mock-request"},
            json={
                "id": "resp_mock",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-6.1-sol",
                "reasoning": {"effort": actual_effort} if actual_effort else None,
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_mock",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "annotations": [],
                                "text": json.dumps(
                                    {
                                        "verdict": "correct",
                                        "evidence": "She wants a bridge for her car.",
                                        "response": "Yes, she wants to make a bridge.",
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 20,
                    "total_tokens": 50,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    with OpenAI(
        api_key="mock-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    ) as client:
        result = run_model_probe(client, effort, evidence_mode="mock")
    assert result["mode"] == "mock"
    assert result["api_smoke_ok"] == config_ok
    assert result["config_matches"] == config_ok
    assert result["sample_matches_reference"]
    assert result["usage"]["total_tokens"] == 50
    assert result["qualification"] == "not_granted"
    assert len(requests) == 1  # Only a mock transport call, not actual API evidence.


def test_missing_key_is_reported_without_instantiating_client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert not preflight()["api_key_present"]
    assert live_probe("low", "test", "test")["mode"] == "live_not_started"


def test_fastapi_pydantic_contract_exports_without_starting_server():
    app = FastAPI()

    @app.post("/contract-only")
    def unused(body: SubmittedInput):
        return body

    schema = app.openapi()
    assert schema["components"]["schemas"]["SubmittedInput"]["additionalProperties"] is False


def test_sqlalchemy_sqlite_transaction_rollback_in_explicit_temporary_directory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mock.sqlite3'}")
    metadata = MetaData()
    records = Table("compatibility_only", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    with pytest.raises(RuntimeError), engine.begin() as connection:
        connection.execute(records.insert().values(id=1))
        raise RuntimeError("injected rollback")
    with engine.connect() as connection:
        assert connection.execute(select(records)).all() == []
    engine.dispose()


def test_explicit_temporary_paths_reject_traversal_and_symlink_escape(tmp_path):
    from project_j.paths import check_path

    assert check_path(tmp_path, "simulation/mock.sqlite3", "simulation").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        check_path(tmp_path, "../family.sqlite3", "simulation")
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        check_path(tmp_path, "escape/file", "adult_check")
    with pytest.raises(ValueError, match="family"):
        check_path(tmp_path, "family.sqlite3", "family")


def test_network_guard_makes_offline_check_explicit():
    import socket

    with socket.socket() as sock, pytest.raises(AssertionError, match="real network"):
        sock.connect(("127.0.0.1", 443))


def test_probe_failure_redacts_error_body_but_keeps_requested_config(monkeypatch):
    import project_j.probe as probe

    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-key")

    def reject_client(**kwargs):
        assert kwargs["max_retries"] == 0
        raise RuntimeError("mock-only-key must not be exposed")

    monkeypatch.setattr(probe, "OpenAI", reject_client)
    result = live_probe("medium", "mock-account", "mock-project")
    assert result["requested_effort"] == "medium"
    assert result["actual_config"] == "未验证"
    assert "mock-only-key" not in json.dumps(result)
    assert result["captured_at"] and result["elapsed_ms"] >= 0
