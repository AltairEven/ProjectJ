import base64
import io
import json
import os
import sys

import numpy as np
from helpers import IDENTITY, FakeEngine

from project_j_tts.model import ModelError
from project_j_tts.serve import protocol_stdout, run, to_pcm16


def line(**message):
    return json.dumps(message).encode() + b"\n"


def say(request_id, text, seed=None):
    return line(id=request_id, op="synthesize", text=text, seed=seed)


def serve(*lines, engine=None):
    engine = engine or FakeEngine()
    out = io.BytesIO()
    code = run(lambda: engine, io.BytesIO(b"".join(lines)), out)
    return code, engine, [json.loads(x) for x in out.getvalue().splitlines()]


def test_ready_then_chunks_then_done():
    code, engine, replies = serve(say("a", "你好", seed=7))
    ready, *chunks, done = replies
    assert code == 0 and ready == {
        "ready": True,
        **IDENTITY,
        "sample_rate": 24000,
        "load_ms": 5,
        "memory_bytes": 123,
    }
    expected = to_pcm16(np.full(2400, 0.5, dtype=np.float32))
    assert [(c["id"], c["chunk"]) for c in chunks] == [("a", 0), ("a", 1)]
    assert all(base64.b64decode(c["audio"]) == expected for c in chunks) and len(expected) == 4800
    assert done == {
        "id": "a",
        "done": True,
        "chunks": 2,
        "audio_seconds": 0.2,
        "truncated": False,
        "memory_bytes": 123,
    }
    assert engine.seeds == [7]


def test_cancel_stops_generation_and_the_next_request_is_served():
    _, engine, replies = serve(say("a", "long"), line(id="a", op="cancel"), say("b", "hi"))
    cancelled = next(i for i, r in enumerate(replies) if r.get("cancelled"))
    assert replies[cancelled]["id"] == "a" and 1 <= replies[cancelled]["chunks"] < 200
    assert "long" in engine.closed_texts
    after = replies[cancelled + 1 :]
    assert all(r["id"] == "b" for r in after)
    assert after[-1]["done"] and after[-1]["chunks"] == 2


def test_bad_requests_fail_alone_and_the_process_keeps_serving():
    _, _, replies = serve(
        say("e", "   "), say("t", "x" * 1001), b"not json\n", say("x", "boom"), say("ok", "hi")
    )
    errors = [(r["id"], r["error"]) for r in replies if "error" in r]
    assert errors == [("e", "empty_text"), ("t", "too_long"), (None, "failed"), ("x", "failed")]
    assert replies[-1]["id"] == "ok" and replies[-1]["done"]


def test_truncation_is_reported_and_idle_cancel_is_acknowledged():
    _, _, replies = serve(say("l", "loop"), line(id="z", op="cancel"))
    assert next(r for r in replies if r.get("done"))["truncated"] is True
    assert replies[-1] == {"id": "z", "cancelled": True, "chunks": None}


def test_unloadable_models_report_not_ready():
    def factory():
        raise ModelError("digest_mismatch", "model.safetensors")

    out = io.BytesIO()
    assert run(factory, io.BytesIO(), out) == 1
    assert json.loads(out.getvalue()) == {
        "ready": False,
        "error": "digest_mismatch",
        "detail": "model.safetensors",
    }


def test_library_prints_go_to_stderr_not_the_protocol(capfd):
    saved = os.dup(1), sys.stdout
    try:
        protocol = protocol_stdout()
        print("library chatter")
        protocol.write(b'{"ready": true}\n')
        protocol.flush()
        captured = capfd.readouterr()
        assert captured.out == '{"ready": true}\n' and "library chatter" in captured.err
    finally:
        os.dup2(saved[0], 1)
        os.close(saved[0])
        sys.stdout = saved[1]


def test_pcm16_clips_and_rounds():
    assert (
        to_pcm16(np.array([0.0, 0.5, 1.5, -2.0], dtype=np.float32))
        == np.array([0, 16384, 32767, -32767], dtype="<i2").tobytes()
    )
