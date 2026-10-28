"""Sealing a class writes the learner's word record; words can be looked up again outside class."""

import asyncio
import logging
from uuid import uuid4

from support import deliver_all, finish_reading, read_story, start

from project_j.application.documents import identity
from project_j.contracts import RevisionRef
from project_j.learning.vocabulary import VocabularyBody


def record(app):
    saved = app.store.current(identity(app.learner_id, "vocabulary"), app.learner_id)
    return saved, VocabularyBody.model_validate(saved.body)


def mark_and_finish(app, word, sentence):
    core = asyncio.run(start(app, str(uuid4())))
    deliver_all(core)
    core.control(str(uuid4()), "start_reading")
    core.mark_word(str(uuid4()), word, sentence, True)
    finish_reading(app, core)
    return core


def test_sealing_writes_the_record_and_a_second_lesson_counts_exits(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = mark_and_finish(application, "bridge", "She wants a bridge for her car.")
    core.control(str(uuid4()), "stop")
    fact = application.seal()
    assert "vocabulary update new=1 marked=1 exits=0 mastered=0 total=1 saved=true" in caplog.text
    assert "She wants" not in caplog.text
    saved, body = record(application)
    (entry,) = body.words
    assert entry.lemma == "bridge" and entry.definition and entry.audio["path"].endswith(".wav")
    assert entry.marks[0].sentence == "She wants a bridge for her car."
    assert entry.marks[0].course_title == "The Paper Bridge"
    assert application.word_asset(application.learner_id, "bridge")[:4] == b"RIFF"
    assert "vocabulary" not in str(fact.body)  # the sealed fact's end carries no box
    # A retake of the same story without marking: one exit for the word met again.
    core = asyncio.run(start(application, "start2"))
    deliver_all(core)
    read_story(core)
    core.control(str(uuid4()), "stop")
    application.seal()
    assert "vocabulary update new=0 marked=0 exits=1 mastered=0 total=1 saved=true" in caplog.text
    saved, body = record(application)
    assert saved.revision == 2
    assert (body.words[0].exit_count, body.words[0].unmarked_appearances) == (
        1,
        ("The Paper Bridge",),
    )


def test_a_stopped_class_with_pending_words_saves_them_without_a_definition(application):
    core = asyncio.run(start(application))
    deliver_all(core)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "s", True)
    core.control(str(uuid4()), "stop")  # never finished reading; nothing looked up
    application.seal()
    _, body = record(application)
    assert body.words[0].definition is None and body.words[0].status == "active"
    assert body.words[0].exit_count == 0 and body.words[0].audio is None
    listed = application.dashboard(application.learner_id)["vocabulary"]
    assert listed[0]["lemma"] == "bridge" and listed[0]["weight"] == 1


def test_regenerate_outside_class_fills_the_definition(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    core = asyncio.run(start(application))
    deliver_all(core)
    core.control(str(uuid4()), "start_reading")
    core.mark_word("m1", "bridge", "She wants a bridge for her car.", True)
    core.control(str(uuid4()), "stop")
    application.seal()  # saved without a definition (never looked up)
    asyncio.run(application.regenerate_entry("r1", application.learner_id, "bridge"))
    assert "vocabulary regenerate outcome=ok audio=true" in caplog.text
    saved, body = record(application)
    assert saved.revision == 2 and body.words[0].definition
    assert body.words[0].audio["path"].endswith(".wav")
    assert body.words[0].marks[0].sentence == "She wants a bridge for her car."
    assert application.word_asset(application.learner_id, "bridge")[:4] == b"RIFF"
    asyncio.run(application.regenerate_entry("r1", application.learner_id, "bridge"))
    assert record(application)[0].revision == 2  # the same request is not redone


def test_evaluation_request_carries_no_vocabulary(application):
    from project_j.evaluation.assessment import evaluation_request

    core = mark_and_finish(application, "bridge", "She wants a bridge for her car.")
    core.control(str(uuid4()), "stop")
    fact = application.seal()
    course = application.store.read(
        RevisionRef.model_validate(fact.body["course_ref"]), application.learner_id
    )
    context = evaluation_request(fact.body, course.body, [], None).context
    assert "vocabulary" not in context and "word_marked" not in str(context)


def test_a_word_marked_again_keeps_its_recorded_audio(application):
    core = mark_and_finish(application, "bridge", "She wants a bridge for her car.")
    core.control(str(uuid4()), "stop")
    application.seal()
    first = record(application)[1].words[0].audio
    assert first["path"] == "vocabulary/bridge.wav"  # Named by the word (ADR-0027).
    core = mark_and_finish(application, "bridge", "Another bridge for the car.")
    application.word_audio["bridge"] = b"RIFF-another-voice"  # A new sound made in this class.
    core.control(str(uuid4()), "stop")
    application.seal()
    assert record(application)[1].words[0].audio == first
    assert application.word_asset(application.learner_id, "bridge")[:4] == b"RIFF"  # Still reads.


def test_regenerating_a_word_replaces_its_file_and_record(application, monkeypatch):
    core = mark_and_finish(application, "bridge", "She wants a bridge for her car.")
    core.control(str(uuid4()), "stop")
    application.seal()
    before = record(application)[1].words[0].audio

    async def regenerated(lemma):
        return b"RIFF-regenerated"

    monkeypatch.setattr(application, "synthesize", regenerated)
    asyncio.run(application.regenerate_entry("re1", application.learner_id, "bridge"))
    after = record(application)[1].words[0].audio
    assert after["path"] == before["path"] == "vocabulary/bridge.wav"
    assert after["sha256"] != before["sha256"]
    assert application.word_asset(application.learner_id, "bridge") == b"RIFF-regenerated"
