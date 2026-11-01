"""Marking words while reading; the box fills in the background after "I've finished reading"."""

import asyncio
import logging
from datetime import date
from uuid import uuid4

import pytest
from support import deliver_all, finish_reading, regenerate_word, start

from project_j.learning.vocabulary import LookedUp, VocabularyBody, update_after_lesson


def opened(application):
    core = asyncio.run(start(application))
    deliver_all(core)
    return core


def events(core, kind):
    return [e["payload"] for e in core.events if e["kind"] == kind]


def box(core):
    return {entry["surface"]: entry for entry in core.snapshot()["vocabulary"]}


def test_marking_only_while_reading_and_toggles_one_entry(application):
    core = opened(application)
    with pytest.raises(ValueError, match="while you read"):
        core.mark_word("m0", "car", "Mia has a toy car.", True)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "Car", "Mia has a toy car.", True)
    core.mark_word("m2", "car", "She puts her car on the paper.", True)  # twice: one entry
    assert [e["surface"] for e in core.snapshot()["vocabulary"]] == ["car"]
    assert box(core)["car"]["status"] == "marked"
    core.control(str(uuid4()), "pause")
    with pytest.raises(ValueError, match="while you read"):
        core.mark_word("m3", "paper", "x", True)
    core.control(str(uuid4()), "resume")
    core.mark_word("m4", "car", "Mia has a toy car.", False)
    assert core.snapshot()["vocabulary"] == [] and len(events(core, "word_unmarked")) == 1
    core.mark_word("m1", "Car", "Mia has a toy car.", True)  # a replay of m1 changes nothing
    assert core.snapshot()["vocabulary"] == []
    assert core.pending_words() == []


def test_finish_reading_asks_the_first_question_at_once_and_fills_the_box_later(application):
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "She wants a bridge for her car.", True)
    core.mark_word("m2", "folds", "She folds a new piece of paper.", True)
    core.control(str(uuid4()), "finish_reading")
    assert core.task_index == 0
    with pytest.raises(ValueError, match="while you read"):
        core.mark_word("m3", "car", "x", True)

    async def scenario():
        application.after_control(core, "finish_reading")  # the page sees "getting ready" first
        assert all(e["status"] == "pending" for e in core.snapshot()["vocabulary"])
        await asyncio.gather(*application.word_jobs)

    asyncio.run(scenario())
    found = box(core)
    assert found["bridge"]["status"] == "ready" and found["bridge"]["lemma"] == "bridge"
    assert found["bridge"]["audio"] is True and found["bridge"]["definition"]
    assert found["bridge"]["part_of_speech"] == "noun"
    assert application.word_audio_of("bridge")[:4] == b"RIFF"
    assert len([c for c in application.models.calls if c["purpose"] == "vocabulary"]) == 1
    assert application.after_control(core, "skip") is None


def test_after_reading_a_word_can_leave_the_box_but_not_join_it(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "She wants a bridge for her car.", True)
    core.mark_word("m2", "folds", "She folds a new piece of paper.", True)
    finish_reading(application, core)
    core.control(str(uuid4()), "pause")
    with pytest.raises(ValueError, match="while the class runs"):
        core.mark_word("m3", "folds", "She folds a new piece of paper.", False)
    core.control(str(uuid4()), "resume")
    core.mark_word("m4", "folds", "She folds a new piece of paper.", False)
    assert list(box(core)) == ["bridge"]
    with pytest.raises(ValueError, match="while you read"):
        core.mark_word("m5", "folds", "She folds a new piece of paper.", True)
    assert "event word_unmarked" in caplog.text and "marked=False" in caplog.text
    assert "folds" not in caplog.text
    core.control(str(uuid4()), "stop")
    with pytest.raises(ValueError, match="while the class runs"):
        core.mark_word("m6", "bridge", "She wants a bridge for her car.", False)


def test_a_lookup_answer_for_a_word_removed_meanwhile_is_dropped(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "She wants a bridge for her car.", True)
    core.control(str(uuid4()), "finish_reading")
    core.mark_word("m2", "bridge", "She wants a bridge for her car.", False)
    core.set_word("bridge", None, False)  # what a lookup still running would answer
    assert core.snapshot()["vocabulary"] == []
    assert "word lookup dropped" in caplog.text and "bridge" not in caplog.text


def test_a_failed_lookup_marks_words_failed_and_regenerate_recovers(application):
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "s", True)
    application.models.adapters["simulated"].fail_next = True
    finish_reading(application, core)
    assert box(core)["bridge"]["status"] == "failed"
    with pytest.raises(LookupError):
        application.word_audio_of("bridge")
    regenerate_word(application, "bridge")
    assert box(core)["bridge"]["status"] == "ready"
    with pytest.raises(LookupError):
        application.regenerate_word("nope")


def test_words_already_in_the_record_are_reused_without_a_model_call(application):
    looked = LookedUp(
        word="bridge",
        sentence="s",
        lemma="bridge",
        part_of_speech="noun",
        definition="a way to cross over",
        audio=None,
    )
    body, _ = update_after_lesson(VocabularyBody(), (looked,), "bridge.", "Old", date(2026, 10, 1))
    core = opened(application)
    application.vocabulary = body  # as start() loads it from the record
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "Bridge", "s", True)
    finish_reading(application, core)
    entry = box(core)["bridge"]
    assert (entry["status"], entry["definition"], entry["audio"]) == (
        "ready",
        "a way to cross over",
        False,
    )
    assert not [c for c in application.models.calls if c["purpose"] == "vocabulary"]


def test_lookup_logs_counts_never_words(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = opened(application)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "She wants a bridge for her car.", True)
    finish_reading(application, core)
    text = caplog.text
    assert "event word_marked" in text and "marked=True" in text
    assert "vocabulary lookup words=1 reused=0 requested=1 ok=1 failed=0" in text
    assert "vocabulary synth outcome=" in text
    assert "bridge" not in text and "She wants" not in text


def test_recorded_audio_of_words_in_this_story_is_loaded_before_class(application):
    from project_j.application.documents import identity
    from project_j.platform.storage import SaveKey

    wav = b"RIFF" + bytes(40)
    looked = LookedUp(
        word="bridge",
        sentence="s",
        lemma="bridge",
        part_of_speech="noun",
        definition="a way to cross over",
        audio=application.files.put("vocabulary/bridge.wav", wav),
    )
    other = looked.model_copy(update={"word": "zebra", "lemma": "zebra"})
    body, _ = update_after_lesson(
        VocabularyBody(), (looked, other), "bridge zebra.", "Old", date(2026, 10, 1)
    )
    key = SaveKey(
        operation_id=identity("test", "vocabulary"), step_id="vocabulary", source_revision=1
    )
    application.save_vocabulary(None, body, application.learner_id, key, "test record")
    core = opened(application)
    assert application.word_audio_of("bridge") == wav  # in the story: read before class
    with pytest.raises(LookupError):
        application.word_audio_of("zebra")  # not in this story: left on disk
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "Bridge", "s", True)
    finish_reading(application, core)
    entry = box(core)["bridge"]
    assert (entry["status"], entry["audio"], entry["definition"]) == (
        "ready",
        True,
        "a way to cross over",
    )
    assert not [c for c in application.models.calls if c["purpose"] == "vocabulary"]
