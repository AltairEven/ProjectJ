import asyncio
import logging

from fastapi.testclient import TestClient
from support import LEARNER, complete_story

from project_j.application.configuration import Settings
from project_j.interface.api import create_app


def test_reading_attempts_record_their_scoring_time(application):
    _, fact = asyncio.run(complete_story(application))
    attempts = fact.body["attempts"]
    readings = [a for a in attempts if a["purpose"] == "read_aloud"]
    assert readings and all(a["scoring_ms"] >= 0 for a in readings)
    assert all("scoring_ms" not in a for a in attempts if a["purpose"] != "read_aloud")


def test_each_link_logs_metadata_but_never_what_the_child_said(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    _, fact = asyncio.run(complete_story(application))
    text = caplog.text
    for link in ("event input", "event allowed_action", "event delivery", "event end"):
        assert link in text
    assert "model classroom_judgment" in text and "elapsed_ms=" in text
    assert "pronunciation task=read-aloud-1 status=scored scoring_ms=" in text
    assert f"seal activity={fact.body['activity_id']}" in text
    said = [i["text"] for a in fact.body["attempts"] for i in a["inputs"] if i.get("text")]
    assert said
    for words in (*said, "听到了，先读故事再试纸桥。", "Mia has a toy car"):
        assert words not in text


def test_http_requests_and_refusals_are_logged_without_the_token(application, caplog):
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "孩子说很好玩。", [])
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(application.evaluate("e1", LEARNER, activity, "after_class"))
    stale = {"object_id": "not-the-evaluation", "revision": 1}
    body = {"request_id": "ack1", "learner_id": LEARNER, "activity_id": activity}
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}
        acknowledged = client.post(
            "/api/evaluation/acknowledge", headers=auth, json=body | {"evaluation_ref": stale}
        )
        assert acknowledged.status_code == 409
        client.get("/api/dashboard", headers={"Authorization": "Bearer wrong"})
    text = caplog.text
    assert "POST /api/evaluation/acknowledge 409" in text
    assert "refused POST /api/evaluation/acknowledge 409 Conflict" in text
    assert "model evaluation" in text and "GET /api/dashboard 403" in text
    assert application.token not in text and "孩子说很好玩" not in text


def test_error_descriptions_never_echo_validated_or_stored_content():
    from pydantic import BaseModel
    from sqlalchemy.exc import IntegrityError

    from project_j.interface.api import describe

    class Answer(BaseModel):
        n: int

    try:
        Answer(n="孩子的回答")
    except ValueError as error:
        assert describe(error) == "ValidationError(1 errors)"
    stored = IntegrityError("INSERT INTO documents VALUES (?)", ("孩子的回答",), Exception("x"))
    assert describe(stored) == "IntegrityError"
    assert describe(LookupError("no active memory core")) == "LookupError: no active memory core"
