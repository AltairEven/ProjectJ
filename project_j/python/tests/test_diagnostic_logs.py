import asyncio
import logging

from fastapi.testclient import TestClient
from support import LEARNER, complete_story, correct_response, deliver_all
from test_program_tasks import to_clicked, wrong_choice

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


def test_rule_judging_logs_the_kind_not_the_answer(application, caplog):
    async def scenario():
        core = await to_clicked(application)
        task = core.current_task()
        caplog.set_level(logging.INFO, logger="project_j")
        await application.adapter.response("hint", None, "help")
        deliver_all(core)
        await application.adapter.response("wrong", wrong_choice(task))
        deliver_all(core)
        await application.adapter.response("right", correct_response(task))
        deliver_all(core)
        return task

    task = asyncio.run(scenario())
    text = caplog.text
    for words in (
        "event hint",
        "purpose=choice",
        "level=1",
        "event rule_judged",
        "judgment=incorrect wrong_count=1 rule_ms=",
        "judgment=correct wrong_count=1 rule_ms=",
        "event task_started",
        "type_version=1",
    ):
        assert words in text
    for words in (*task.choice.options, task.correct_text, *[s.text for s in task.supports]):
        assert words not in text


def test_scoring_and_estimates_log_counts_only(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    _, fact = asyncio.run(complete_story(application))
    activity = fact.body["activity_id"]
    application.feedback("fb1", LEARNER, activity, "孩子说很好玩。", [])
    evaluation = asyncio.run(application.evaluate("e1", LEARNER, activity, "after_class"))
    text = caplog.text
    assert f"evaluation scores activity={activity} rule=4 model=3 pronunciation=" in text
    assert "unknown=" in text
    assert all(entry["reason"] not in text for entry in evaluation.body["scores"])
    assert "minutes=" in text and "types=" in text  # preparation drafted, from start()
    # A first lesson: no scores and no sealed times yet; counts only.
    assert "preparation scores lessons=0 types=0" in text
    assert "preparation calibration types=0 samples=0" in text
