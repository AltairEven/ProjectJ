"""The page marks words, hears them and asks for a new meaning through the class routes."""

from uuid import uuid4

from fastapi.testclient import TestClient
from support import LEARNER, confirmed, deliver_all, prepare_course, settle

from project_j.application.configuration import Settings
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app


def test_word_routes(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        headers = {"Authorization": f"Bearer {app.token}"}
        prepared = prepare_course(app, "prepare")
        confirmed(app, prepared)
        app.start("start", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), True)
        deliver_all(app.core)

        def control(command):
            return client.post(
                "/api/class/control",
                json={"event_id": str(uuid4()), "command": command},
                headers=headers,
            )

        def mark(event_id, word, marked=True):
            return client.post(
                "/api/class/word",
                json={
                    "event_id": event_id,
                    "word": word,
                    "sentence": "She wants a bridge.",
                    "marked": marked,
                },
                headers=headers,
            )

        assert mark("w0", "bridge").status_code == 400  # before "Start reading"
        assert control("start_reading").status_code == 200
        assert mark("w00", "two words").status_code in {400, 422}  # one story word only
        assert mark("w01", "a1").status_code in {400, 422}
        reply = mark("w1", "bridge")
        assert reply.status_code == 200 and reply.json()["vocabulary"][0]["surface"] == "bridge"
        assert control("finish_reading").status_code == 200
        settle(app)
        snapshot = client.get("/api/class/snapshot", headers=headers).json()
        assert snapshot["vocabulary"][0]["status"] == "ready"
        audio = client.get("/api/class/word-audio/bridge", headers=headers)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF" and audio.headers["cache-control"] == "no-store"
        assert client.get("/api/class/word-audio/nope", headers=headers).status_code == 404
        again = client.post(
            "/api/class/word/regenerate", json={"surface": "bridge"}, headers=headers
        )
        assert again.status_code == 202 and again.json()["vocabulary"][0]["status"] == "pending"
        settle(app)
        assert (
            client.get("/api/class/snapshot", headers=headers).json()["vocabulary"][0]["status"]
            == "ready"
        )
        assert (
            client.post(
                "/api/class/word/regenerate", json={"surface": "nope"}, headers=headers
            ).status_code
            == 404
        )
        assert mark("w2", "car").status_code == 400  # reading is over
        app.core.control("stop", "stop")
        settle(app)
