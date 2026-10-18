import base64
import io
import json
import os
import sys
import wave

import pytest

from project_j_scorer.model import ModelError
from project_j_scorer.serve import AudioError, decode_wav, protocol_stdout, run

IDENTITY = {
    "scorer": "local-gop",
    "version": "1",
    "thresholds": "provisional-1",
    "model": "m",
    "revision": "r",
    "device": "cpu",
}


def wav(seconds=1.0, rate=16000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buffer.getvalue()


class Engine:
    identity, load_ms = IDENTITY, 5

    def score(self, audio, reference):
        if reference == "boom":
            raise RuntimeError("engine failure")
        word = {"word": reference, "accuracy": 99.0, "min_phone": 90.0, "error": "none"}
        return {
            "status": "scored",
            "words": [word],
            "accuracy": 99.0,
            "completeness": 100.0,
            "pauses": 0,
            "words_per_minute": 100.0,
        }

    def memory_bytes(self):
        return 42


def serve(*lines, factory=Engine):
    out = io.BytesIO()
    code = run(factory, io.BytesIO(b"".join(line + b"\n" for line in lines)), out)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def request(rid, reference="bridge", audio=None):
    audio = wav() if audio is None else audio
    body = {"id": rid, "audio": base64.b64encode(audio).decode(), "reference": reference}
    return json.dumps(body).encode()


def test_ready_line_then_one_reply_per_request():
    code, lines = serve(request("a"), request("b", "car"))
    assert code == 0 and lines[0] == {"ready": True, **IDENTITY, "load_ms": 5, "memory_bytes": 42}
    assert [line["id"] for line in lines[1:]] == ["a", "b"]
    assert lines[2]["words"][0]["word"] == "car"
    assert all("elapsed_ms" in line for line in lines[1:])


def test_bad_input_and_engine_errors_fail_one_request_and_keep_serving():
    _, lines = serve(
        b"not json", request("x", audio=wav(rate=44100)), request("y", "boom"), request("z")
    )
    failed = {"status": "unknown", "reason": "failed"}
    assert lines[1] == {"id": None, **failed}
    assert lines[2] == {"id": "x", **failed} and lines[3] == {"id": "y", **failed}
    assert lines[4]["id"] == "z" and lines[4]["status"] == "scored"


def test_unloadable_models_report_not_ready():
    def broken():
        raise ModelError("digest_mismatch", "pytorch_model.bin")

    code, lines = serve(request("a"), factory=broken)
    assert code == 1
    assert lines == [{"ready": False, "error": "digest_mismatch", "detail": "pytorch_model.bin"}]


@pytest.mark.parametrize(
    "bad", [wav(channels=2), wav(rate=44100), wav(width=1), b"OggS....", wav(seconds=311)]
)
def test_only_16k_mono_pcm16_wav_within_the_limit_is_accepted(bad):
    with pytest.raises(AudioError):
        decode_wav(bad)
    assert decode_wav(wav(2.0)).shape == (32000,)


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
