"""Opt-in, synthetic single-request Responses probe; no microphone or child inputs."""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

MODEL = "gpt-6.1-sol"


class ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["correct", "incorrect", "unclear"]
    evidence: str
    response: str


def request_parameters(effort: Literal["low", "medium"]) -> dict:
    if effort not in {"low", "medium"}:
        raise ValueError("only adopted low/medium configurations")
    return {
        "model": MODEL,
        "reasoning": {"effort": effort},
        "service_tier": "default",
        "store": False,
        "max_output_tokens": 1024,
        "text_format": ProbeAnswer,
        "input": [
            {
                "role": "developer",
                "content": (
                    "This is a synthetic technical probe, not a child classroom. "
                    "Judge only the given source. Propose one short response; do not change state."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Source: Mia has a toy car. She wants a bridge for her car. "
                    "Question: What does Mia want to make? Answer: a bridge."
                ),
            },
        ],
    }


def preflight() -> dict:
    return {
        "mode": "offline_preflight",
        "api_key_present": bool(os.getenv("OPENAI_API_KEY")),
        "requested_model": MODEL,
        "efforts": ["low", "medium"],
        "service_tier": "default",
        "sdk_auto_retries": 0,
        "actual_api": "未验证",
        "actual_voice": "未验证",
        "account_zdr": "未验证",
    }


def run_model_probe(
    client: OpenAI,
    effort: Literal["low", "medium"],
    *,
    evidence_mode: Literal["technical_actual_request", "mock"] = "technical_actual_request",
) -> dict:
    started = time.perf_counter()
    captured_at = datetime.now(UTC).isoformat()
    response = client.responses.parse(**request_parameters(effort))
    parsed = response.output_parsed
    actual_effort = response.reasoning.effort if response.reasoning else None
    config_matches = (
        response.model == MODEL and actual_effort == effort and response.service_tier == "default"
    )
    return {
        "mode": evidence_mode,
        "captured_at": captured_at,
        "sample": "synthetic-bridge-1",
        "requested_model": MODEL,
        "requested_effort": effort,
        "requested_service_tier": "default",
        "actual_model": response.model,
        "actual_effort": actual_effort,
        "actual_service_tier": response.service_tier,
        "config_matches": config_matches,
        "status": response.status,
        "structured_answer": parsed.model_dump() if parsed else None,
        "sample_matches_reference": parsed is not None and parsed.verdict == "correct",
        "request_id": response._request_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "usage": response.usage.model_dump() if response.usage else None,
        "api_smoke_ok": config_matches and response.status == "completed" and parsed is not None,
        "qualification": "not_granted",
        "voice_latency": "未验证",
        "account_zdr": "未验证",
    }


def live_probe(effort: Literal["low", "medium"], account_label: str, project_label: str) -> dict:
    if not os.getenv("OPENAI_API_KEY"):
        return {"mode": "live_not_started", "actual_api": "未验证", "missing": "OPENAI_API_KEY"}
    # No .env loading, provider overrides, credentials logging, automatic retry or tools.
    started = time.perf_counter()
    attempted_at = datetime.now(UTC).isoformat()
    try:
        with OpenAI(base_url="https://api.openai.com/v1", timeout=10, max_retries=0) as client:
            result = run_model_probe(client, effort)
    except Exception as exc:
        result = {
            "mode": "technical_actual_request",
            "result": "failed",
            "error_type": type(exc).__name__,  # No error body, headers, or credentials.
            "captured_at": attempted_at,
            "sample": "synthetic-bridge-1",
            "requested_model": MODEL,
            "requested_effort": effort,
            "requested_service_tier": "default",
            "actual_config": "未验证",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "usage": None,
            "qualification": "not_granted",
        }
    return result | {"account_label": account_label, "project_label": project_label}


def main() -> int:
    """Offline by default; --live explicitly opts into one paid synthetic API request."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--effort", choices=["low", "medium"], default="low")
    parser.add_argument("--account-label")
    parser.add_argument("--project-label")
    args = parser.parse_args()
    if args.live and (not args.account_label or not args.project_label):
        parser.error(
            "--live requires nonsecret --account-label and --project-label; no keys in labels"
        )
    result = (
        live_probe(args.effort, args.account_label, args.project_label)
        if args.live
        else preflight()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if args.live and not result.get("api_smoke_ok") else 0
