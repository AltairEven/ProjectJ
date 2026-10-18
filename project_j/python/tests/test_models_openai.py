import asyncio
import json

import httpx2
import pytest

from project_j.classroom.judgment import ClassroomJudgment
from project_j.platform.models.contracts import Binding, ModelRequest
from project_j.platform.models.openai_api import OpenAIAdapter, model_matches, solid_png

KEY = "sk-mock-0123456789"
BINDING = Binding(adapter="openai", model="gpt-6.1-sol", effort="low")
REQUEST = ModelRequest(
    "classroom_judgment", "judge", {"evidence": "e", "answer": "a bridge"}, ClassroomJudgment
)


def body(text, model="gpt-6.1-sol", effort="low", tier="default", status="completed"):
    return {
        "id": "resp_mock",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": model,
        "reasoning": {"effort": effort} if effort else None,
        "service_tier": tier,
        "output": [
            {
                "type": "message",
                "id": "msg",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "annotations": [], "text": text}],
            }
        ],
        "usage": {
            "input_tokens": 30,
            "output_tokens": 20,
            "total_tokens": 50,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    }


GOOD = json.dumps({"judgment": "correct", "evidence": "e", "text": "对"})


def adapter(handler, key=KEY):
    return OpenAIAdapter(
        key, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )


def run(handler, request=REQUEST, binding=BINDING, key=KEY):
    return asyncio.run(adapter(handler, key).invoke(request, binding))


def test_request_body_is_strict_unstored_default_tier_and_key_only_in_header():
    seen = {}

    def respond(request):
        seen["payload"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx2.Response(200, headers={"x-request-id": "req-1"}, json=body(GOOD))

    reply = run(respond)
    payload = seen["payload"]
    assert payload["model"] == "gpt-6.1-sol" and payload["reasoning"]["effort"] == "low"
    assert payload["store"] is False and payload["service_tier"] == "default"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert seen["auth"] == f"Bearer {KEY}" and KEY not in json.dumps(payload)
    assert reply.failure is None and reply.data["judgment"] == "correct"
    assert reply.request_id == "req-1" and reply.usage["total_tokens"] == 50
    assert {name: check.status for name, check in reply.actual.items()} == {
        "model": "verified",
        "effort": "verified",
        "service_tier": "verified",
    }


def test_snapshot_model_id_counts_as_requested_model():
    assert model_matches("gpt-6.1-sol", "gpt-6.1-sol-2026-09-01")
    assert not model_matches("gpt-6.1-sol", "gpt-6.1-sol-mini")
    reply = run(lambda r: httpx2.Response(200, json=body(GOOD, model="gpt-6.1-sol-2026-09-01")))
    assert reply.actual["model"].status == "verified"


@pytest.mark.parametrize(
    "kwargs,name,status",
    [
        ({"effort": None}, "effort", "unverified"),
        ({"effort": "medium"}, "effort", "mismatch"),
        ({"model": "gpt-5"}, "model", "mismatch"),
        ({"tier": None}, "service_tier", "unverified"),
    ],
)
def test_actual_config_is_verified_unverified_or_mismatch(kwargs, name, status):
    reply = run(lambda r: httpx2.Response(200, json=body(GOOD, **kwargs)))
    assert reply.actual[name].status == status


@pytest.mark.parametrize(
    "code,error_code,kind",
    [
        (401, "invalid_api_key", "auth"),
        (429, "insufficient_quota", "quota"),
        (403, "model_not_found", "unavailable"),
        (404, "model_not_found", "unavailable"),
    ],
)
def test_failures_map_to_kinds_without_error_text(code, error_code, kind):
    message = f"Incorrect API key provided: {KEY[:6]}***{KEY[-4:]}"

    def respond(request):
        return httpx2.Response(code, json={"error": {"message": message, "code": error_code}})

    reply = run(respond)
    assert reply.failure == kind and reply.data is None
    assert KEY[-4:] not in repr(reply)


def test_missing_key_never_calls_the_service():
    def respond(request):
        raise AssertionError("no request without a key")

    assert run(respond, key=None).failure == "auth"


def test_incomplete_refused_and_invalid_output():
    incomplete = body(GOOD, status="incomplete")
    assert run(lambda r: httpx2.Response(200, json=incomplete)).failure == "incomplete"
    assert run(lambda r: httpx2.Response(200, json=body("not json"))).failure == "invalid_output"
    refusal = body(GOOD)
    refusal["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
    assert run(lambda r: httpx2.Response(200, json=refusal)).failure == "refused"


def test_probe_reports_each_item_and_image_input():
    def respond(request):
        payload = json.loads(request.content)
        content = payload["input"][-1]["content"]
        if isinstance(content, list):  # image probe
            assert content[1]["image_url"].startswith("data:image/png;base64,")
            return httpx2.Response(200, json=body(json.dumps({"color": "Red"}), effort=None))
        effort = payload["reasoning"]["effort"]
        answer = {
            "verdict": "correct",
            "evidence": "She wants a bridge for her car.",
            "response": "Yes.",
        }
        return httpx2.Response(200, json=body(json.dumps(answer), effort=effort))

    report = asyncio.run(adapter(respond).probe("gpt-6.1-sol", ("low", "medium")))
    statuses = {name: item.status for name, item in report.items.items()}
    assert statuses["auth"] == statuses["model"] == statuses["structured_output"] == "verified"
    assert statuses["effort:low"] == statuses["effort:medium"] == "verified"
    assert statuses["cancellation"] == "verified"
    assert "服务端" in report.items["cancellation"].detail
    assert statuses["image_input"] == "verified" and statuses["audio"] == "not_probed"
    assert not report.simulated and KEY not in report.model_dump_json()


def test_probe_stops_at_auth_failure():
    def respond(request):
        return httpx2.Response(
            401, json={"error": {"message": "bad key", "code": "invalid_api_key"}}
        )

    report = asyncio.run(adapter(respond).probe("gpt-6.1-sol", ("low",)))
    assert report.items["auth"].status == "failed"
    assert report.items["structured_output"].status == "not_probed"


def test_solid_png_is_an_unambiguous_red_png():
    import struct
    import zlib

    data = solid_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82")
    # A real probe read an 8x8 (220, 30, 30) square as orange: keep it large and pure red.
    width, height = struct.unpack(">II", data[16:24])
    assert width == height >= 64
    pixels = zlib.decompress(data[data.index(b"IDAT") + 4 : data.index(b"IEND") - 8])
    assert pixels[1:4] == bytes((255, 0, 0))


def test_one_adapter_serves_many_calls_and_closes_on_request():
    calls = []

    def respond(request):
        calls.append(1)
        return httpx2.Response(200, json=body(GOOD))

    instance = adapter(respond)

    async def scenario():
        assert (await instance.invoke(REQUEST, BINDING)).failure is None
        assert (await instance.invoke(REQUEST, BINDING)).failure is None
        await instance.aclose()

    asyncio.run(scenario())
    assert len(calls) == 2
