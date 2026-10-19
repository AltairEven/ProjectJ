import asyncio

import pytest
from fastapi.testclient import TestClient
from support import start

from project_j.application.configuration import Settings
from project_j.interface.api import create_app, speech_body
from project_j.platform.tts.contracts import SynthesisFailed


class FakeSynthesizer:
    service, version, simulated, model, voice, sample_rate = "fake", "1", True, None, "v", 24000

    def __init__(self, chunks=(b"\x01\x00" * 4, b"\x02\x00" * 4), fail_at=None):
        self.chunks, self.fail_at, self.texts, self.closed = chunks, fail_at, [], False

    def reasons(self):
        return []

    async def stream(self, text, *, seed=None):
        self.texts.append(text)
        try:
            for index, chunk in enumerate(self.chunks):
                if index == self.fail_at:
                    raise SynthesisFailed("failed")
                yield chunk
        finally:
            self.closed = True


def speech(application, synthesizer, action_id=None, token=None):
    application.synthesizer = synthesizer
    # Storage is closed during a class, so the app initializes first and the class starts after.
    with TestClient(create_app(Settings(), application)) as client:
        core = asyncio.run(start(application))
        token = token or application.start_requests["start1"]["result"]["student_token"]
        action = core.actions[core.pending]
        reply = client.get(
            f"/api/student/speech/{action_id or action.action_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    return reply, action


def test_speech_streams_the_approved_action_text(application):
    fake = FakeSynthesizer()
    reply, action = speech(application, fake)
    assert reply.status_code == 200 and reply.content == b"".join(fake.chunks)
    content_type = reply.headers["content-type"]
    assert content_type.startswith("audio/L16") and "rate=24000" in content_type
    assert fake.texts == [action.text]


def test_speech_refuses_unknown_actions(application):
    reply, _ = speech(application, FakeSynthesizer(), action_id="not-approved")
    assert reply.status_code == 404


def test_speech_refuses_a_wrong_token(application):
    reply, _ = speech(application, FakeSynthesizer(), token="wrong")
    assert reply.status_code == 403


@pytest.mark.parametrize("synthesizer", [FakeSynthesizer(fail_at=0), FakeSynthesizer(chunks=())])
def test_speech_without_any_audio_is_503(application, synthesizer):
    reply, _ = speech(application, synthesizer)
    assert reply.status_code == 503 and reply.json()["reason"] == "failed"
    assert synthesizer.closed


def test_closing_the_body_closes_the_synthesizer_stream():
    async def scenario():
        fake = FakeSynthesizer()
        stream = fake.stream("x")
        body = speech_body(stream, await anext(stream))
        assert await anext(body) == fake.chunks[0]
        await body.aclose()
        return fake

    assert asyncio.run(scenario()).closed
