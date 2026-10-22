"""The classroom core reads English pleas and confirmations and keeps Chinese away (ADR-0021)."""

import asyncio
import logging

import pytest
from support import deliver_all, read_story, start
from test_task_types import definition, fill_task

from project_j.classroom.harness import is_confirmation, is_help_request
from project_j.classroom.responses import judge
from project_j.english import ENGLISH_ONLY


def point_task():
    return definition("point", dimension_id="science.observation.report")


@pytest.mark.parametrize(
    "text",
    [
        "Help me",
        "I need help",
        "I don't understand the question",
        "i dont understand",
        "I still don’t understand",
    ],
)
def test_english_pleas_are_help(text):
    assert is_help_request(definition(), text, "unknown")


@pytest.mark.parametrize("text", ["I don't know", "She helped Mia.", "帮我一下"])
def test_help_word_inside_an_answer_is_not_a_plea(text):
    assert not is_help_request(definition(), text, "unknown")


def test_a_report_counts_only_a_bare_plea():
    assert is_help_request(point_task(), "help me please", "unknown")
    assert not is_help_request(point_task(), "I needed help from my mom", "unknown")


def test_not_understanding_the_question_is_a_plea_in_a_report_but_not_knowing_is_a_report():
    assert is_help_request(point_task(), "I don't understand the question.", "unknown")
    assert is_help_request(point_task(), "I do not understand", "unknown")
    assert not is_help_request(point_task(), "I don't know what happened.", "unknown")


@pytest.mark.parametrize(
    ("text", "confirmed"),
    [("Yes", True), ("OK, got it.", True), ("I heard you.", True), ("听到了", False)],
)
def test_confirmation_is_english(text, confirmed):
    assert is_confirmation(text) is confirmed
    assert not is_confirmation("a bridge")


def test_full_width_punctuation_is_not_chinese_input():
    assert judge(fill_task(), {"blanks": ["puts。"]})
    with pytest.raises(ValueError, match=ENGLISH_ONLY):
        judge(fill_task(), {"blanks": ["放"]})
    assert is_confirmation("Yes，I heard.")


def test_a_chinese_judgment_text_is_rejected_and_logged(application, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        core = await start(application)
        deliver_all(core)
        read_story(application.core)
        evidence = core.current_task().evidence
        monkeypatch.setattr(
            application.models.adapters["simulated"],
            "respond",
            lambda request: {"judgment": "correct", "evidence": evidence, "text": "做得好"},
        )
        result = await application.adapter.text("chinese-reply", "a bridge")
        assert result["ack"] == "rejected_proposal"
        rejected = [e for e in core.events if e["kind"] == "proposal_rejected"]
        assert rejected[-1]["payload"]["reason"] == "language"
        assert core.task_index == 0 and core.actions[core.pending].kind == "clarification"

    asyncio.run(scenario())
    assert "classroom proposal rejected reason=language" in caplog.text
    assert "做得好" not in caplog.text
