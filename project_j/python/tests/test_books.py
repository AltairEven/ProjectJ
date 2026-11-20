"""Expanded reading: a taken lesson becomes a book in readings/, chapter by chapter."""

import asyncio
import hashlib
import io
import logging
import zipfile
from dataclasses import replace
from datetime import date

import pytest
from support import LEARNER, start
from test_confirm_and_start import end_class

from project_j.application import books
from project_j.application.books import (
    STOPPED,
    BookRecord,
    book_file,
    retry_book,
    revise_chapter,
    start_book,
    start_revision,
    stop_books,
    write_book,
)
from project_j.application.books import (
    books as book_rows,
)
from project_j.application.documents import identity
from project_j.application.progress import run
from project_j.contracts import RevisionRef
from project_j.learning.vocabulary import LookedUp, VocabularyBody, update_after_lesson
from project_j.platform.models.contracts import AdapterReply, solid_png
from project_j.platform.storage import Conflict, SaveKey
from project_j.preparation.difficulty import targets
from project_j.preparation.folders import lesson_of


def noop(done, total):
    pass


def taken(app) -> RevisionRef:
    """A course taught and sealed: the only kind a book is made from."""
    asyncio.run(start(app))
    end_class(app)
    fact = app.store.list_current(LEARNER, "fact")[0]
    return RevisionRef.model_validate(fact.body["course_ref"])


def load(app, book: str) -> BookRecord:
    return BookRecord.model_validate_json(app.files.read(f"readings/{book}/book.json"))


def epub_names(app, book: str) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(app.files.read(f"readings/{book}/book.epub"))) as epub:
        return epub.namelist()


def digests(app, folder: str) -> dict[str, str]:
    root = app.store.run / folder
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def lesson_folder(app, course_ref) -> str:
    return lesson_of(app, LEARNER, course_ref).body["folder"]


def plant_words(app, lemmas, mastered=(), key="test"):
    looked = [
        LookedUp(word=w, sentence="s", lemma=w, part_of_speech="noun", definition="d", audio=None)
        for w in (*lemmas, *mastered)
    ]
    body, _ = update_after_lesson(VocabularyBody(), tuple(looked), "", "Old", date(2026, 10, 1))
    words = tuple(
        e.model_copy(update={"status": "mastered"}) if e.lemma in mastered else e
        for e in body.words
    )
    saved, _ = app.vocabulary_record(LEARNER)
    key = SaveKey(operation_id=identity(key, "vocabulary"), step_id="vocabulary", source_revision=1)
    app.save_vocabulary(saved, VocabularyBody(words=words), LEARNER, key, "test record")


def calls(app, monkeypatch, change=None):
    """Every reading request the simulated model gets; change(request) may answer instead."""
    adapter = app.models.adapters["simulated"]
    original, seen = adapter.invoke, []

    async def invoke(request, binding):
        if request.purpose == "reading":
            seen.append(request)
            if change and (reply := await change(request, len(seen))) is not None:
                return reply
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)
    return seen


def write(app, book):
    asyncio.run(write_book(app, LEARNER, book, noop))


def test_a_taken_lesson_becomes_a_ready_book(application, caplog):
    app = application
    course_ref = taken(app)
    folder = lesson_folder(app, course_ref)
    before = digests(app, folder)
    profile_before = app.store.current(identity(LEARNER, "profile"), LEARNER).body
    vocabulary_before = app.vocabulary_record(LEARNER)[1]
    with caplog.at_level(logging.INFO, logger="project_j"):
        book = start_book(app, LEARNER, course_ref, "little")
        write(app, book)
    record = load(app, book)
    assert record.status == "ready" and 4 <= len(record.chapters) <= 6
    assert record.target == targets("500L")["little"] and record.lesson == folder
    assert all(state == "drawn" for state in record.pictures.values())
    assert record.calls and record.calls[0]["step"] == "outline"
    names = epub_names(app, book)
    assert "OEBPS/chapter-1.xhtml" in names and "mimetype" == names[0]
    assert len([n for n in names if n.startswith("OEBPS/images/")]) == len(record.chapters)
    assert digests(app, folder) == before  # the lesson folder is untouched
    assert app.store.current(identity(LEARNER, "profile"), LEARNER).body == profile_before
    assert app.vocabulary_record(LEARNER)[1] == vocabulary_before  # the book counts nothing
    assert f"book start book={book}" in caplog.text and "challenge=little" in caplog.text
    assert f"book done book={book}" in caplog.text and "epub_bytes=" in caplog.text
    assert "The Longer Story" not in caplog.text and "red coat" not in caplog.text  # no text
    (row,) = book_rows(app)
    assert row["state"] == "ready" and row["title"] == "The Longer Story"
    assert row["words"] > 0 and len(row["chapters"]) == len(record.chapters)
    assert book_file(app, book)[:2] == b"PK"


def test_every_active_word_reaches_the_outline_request(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    lemmas = [f"word{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(120)]
    plant_words(app, lemmas, mastered=("zebra",))
    seen = calls(app, monkeypatch)
    with caplog.at_level(logging.INFO, logger="project_j"):
        write(app, start_book(app, LEARNER, course_ref, "same"))
    offered = [word["lemma"] for word in seen[0].context["words"]]
    assert sorted(offered) == sorted(lemmas) and "zebra" not in offered
    assert "offered=120" in caplog.text and "missing=0" in caplog.text
    assert "wordaa" not in caplog.text  # words stay out of the log


def test_a_failing_chapter_is_rewritten_alone(application, monkeypatch):
    app = application
    course_ref = taken(app)

    async def short_once(request, count):
        if request.context.get("number") == 2 and "rejected" not in request.context:
            return AdapterReply(
                data={
                    "title": "Too short",
                    "paragraphs": ["Mia ran."],
                    "pictures": [],
                    "new_picture_paragraph": 1,
                    "new_picture": "Mia.",
                }
            )
        return None

    seen = calls(app, monkeypatch, short_once)
    write(app, start_book(app, LEARNER, course_ref, "same"))
    asked = [r.context["number"] for r in seen if r.context.get("kind") == "chapter"]
    assert asked == [1, 2, 2, 3, 4]
    assert "problems" in seen[3].context and seen[3].context["rejected"]["title"] == "Too short"


def test_a_chapter_failing_twice_fails_the_book_and_keeps_the_others(application, monkeypatch):
    app = application
    course_ref = taken(app)

    async def short(request, count):
        if request.context.get("number") == 3:
            return AdapterReply(
                data={
                    "title": "Too short",
                    "paragraphs": ["Mia ran."],
                    "pictures": [],
                    "new_picture_paragraph": 1,
                    "new_picture": "Mia.",
                }
            )
        return None

    calls(app, monkeypatch, short)
    book = start_book(app, LEARNER, course_ref, "same")
    with pytest.raises(books.BookFailed):
        write(app, book)
    record = load(app, book)
    assert record.status == "writing" and len(record.chapters) == 2


def test_missing_words_after_the_repair_still_finish(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    plant_words(app, ["lantern", "pebble", "meadow", "kettle"])
    adapter = app.models.adapters["simulated"]

    async def forget(request, count):
        if request.context.get("kind") == "chapter" and "pebble" in request.context["words"]:
            context = request.context | {
                "words": [w for w in request.context["words"] if w != "pebble"]
            }
            return AdapterReply(data=adapter.respond(replace(request, context=context)))
        return None

    calls(app, monkeypatch, forget)
    with caplog.at_level(logging.INFO, logger="project_j"):
        book = start_book(app, LEARNER, course_ref, "same")
        write(app, book)
    assert load(app, book).status == "ready"
    assert "missing=1" in caplog.text and "result=repaired" in caplog.text


def test_a_failed_picture_leaves_that_chapter_without_one(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    adapter = app.models.adapters["simulated"]
    original, failed = adapter.invoke, []

    async def invoke(request, binding):
        if request.media == "image" and not failed:
            failed.append(request)
            return AdapterReply(data=None, failure="unavailable")
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)
    book = start_book(app, LEARNER, course_ref, "same")
    with caplog.at_level(logging.INFO, logger="project_j"):
        write(app, book)
    record = load(app, book)
    assert record.status == "ready" and record.pictures[1] == "failed"
    assert f"book picture book={book} chapter=1 result=failed" in caplog.text
    assert f"book picture book={book} chapter=2 result=drawn" in caplog.text
    names = epub_names(app, book)
    assert not any(n.startswith("OEBPS/images/c1-") for n in names)
    assert any(n.startswith("OEBPS/images/c2-") for n in names)


def test_untaken_or_busy_lessons_are_refused(application, monkeypatch):
    app = application
    prepared = asyncio.run(app.prepare("prepare1", LEARNER))
    with pytest.raises(Conflict):  # prepared, never taught
        start_book(app, LEARNER, RevisionRef.model_validate(prepared["course_ref"]), "same")
    course_ref = taken(app)
    for name in ("reading:x", "reference"):
        app.jobs[name] = None
        with pytest.raises(Conflict):
            start_book(app, LEARNER, course_ref, "same")
        del app.jobs[name]
    monkeypatch.setattr(app.core, "mode", "running")  # in class
    with pytest.raises(Conflict):
        start_book(app, LEARNER, course_ref, "same")


def test_class_start_stops_the_book_and_retry_resumes(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    book = start_book(app, LEARNER, course_ref, "same")

    async def block(request, count):
        if request.context.get("number") == 3:
            await asyncio.sleep(3600)
        return None

    async def scenario():
        seen = calls(app, monkeypatch, block)
        run(app, LEARNER, f"reading:{book}", lambda: write_book(app, LEARNER, book, noop))
        while not any(r.context.get("number") == 3 for r in seen):
            await asyncio.sleep(0.01)
        await stop_books(app)

    with caplog.at_level(logging.INFO, logger="project_j"):
        asyncio.run(scenario())
    assert f"reading:{book}" not in app.jobs and app.job_failures[f"reading:{book}"] == STOPPED
    assert f"book stopped book={book} reason=class" in caplog.text
    (row,) = book_rows(app)
    assert row["state"] == "failed" and row["reason"] == STOPPED
    assert len(load(app, book).chapters) == 2
    monkeypatch.undo()
    seen = calls(app, monkeypatch)
    retry_book(app, LEARNER, book)
    write(app, book)
    asked = [r.context.get("number") for r in seen if r.media is None]
    assert asked == [3, 4] and load(app, book).status == "ready"


def test_original_pictures_go_into_their_chapters(application, monkeypatch):
    app = application
    course_ref = taken(app)
    course = app.store.read(course_ref, LEARNER).body
    lesson = lesson_of(app, LEARNER, course_ref).body
    resource = {"resource_id": "pond", "kind": "image", "audience": "student", "stage": "story"}
    resource |= {"paragraph": 1, "content": "a pond at dawn"}
    asset = app.files.put("readings/source/pond.png", solid_png()) | {"media_type": "image/png"}
    found = books.originals(
        course | {"resources": [*course["resources"], resource]},
        lesson | {"files": lesson["files"] | {"pond": asset}},
    )
    assert found == [
        {"picture": "pond", "paragraph": 1, "content": "a pond at dawn", "asset": asset}
    ]
    monkeypatch.setattr(books, "originals", lambda course, lesson: found)
    seen = calls(app, monkeypatch)
    book = start_book(app, LEARNER, course_ref, "same")
    write(app, book)
    assert seen[0].context["pictures"] == [
        {"picture": "pond", "paragraph": 1, "content": "a pond at dawn"}
    ]
    names = epub_names(app, book)
    assert "OEBPS/images/c1-p1.png" in names  # the original, chapter 1 paragraph 1


def test_lesson_without_pictures_or_style(application, monkeypatch):
    app = application
    course_ref = taken(app)
    assert not app.store.read(course_ref, LEARNER).body.get("picture_style")
    seen = calls(app, monkeypatch)
    book = start_book(app, LEARNER, course_ref, "same")
    write(app, book)
    assert seen[0].context["pictures"] == [] and seen[0].context["style"] == ""
    record = load(app, book)
    assert record.status == "ready" and record.outline.style  # the outline wrote a style
    pictures = [r for r in seen if r.media == "image"]
    assert pictures and all(r.context["style"] == record.outline.style for r in pictures)


def test_no_lexile_means_no_target(application, monkeypatch):
    app = application
    course_ref = taken(app)
    monkeypatch.setattr(books, "targets", lambda lexile: {})
    seen = calls(app, monkeypatch)
    book = start_book(app, LEARNER, course_ref, "harder")
    write(app, book)
    assert load(app, book).target is None and seen[0].context["target"] is None


def test_book_file_refuses_bad_ids(application):
    for bad in ("../x", "lesson-001", "lesson-001-2026-10-28-20261120T101500Z/../../y"):
        with pytest.raises(ValueError):
            book_file(application, bad)
    with pytest.raises(LookupError):
        book_file(application, "lesson-001-2026-10-28-20261120T101500Z")


def ready_book(app) -> str:
    book = start_book(app, LEARNER, taken(app), "same")
    write(app, book)
    return book


def revise(app, book, chapter, feedback):
    asyncio.run(revise_chapter(app, LEARNER, book, chapter, feedback, noop))


def test_revising_changes_only_that_chapter(application, monkeypatch, caplog):
    app = application
    book = ready_book(app)
    before, epub_before = load(app, book), app.files.read(f"readings/{book}/book.epub")
    seen = calls(app, monkeypatch)
    with caplog.at_level(logging.INFO, logger="project_j"):
        start_revision(app, LEARNER, book, 2, "Make it longer.")
        revise(app, book, 2, "Make it longer.")
    after = load(app, book)
    assert after.chapters[1] != before.chapters[1] and after.revisions == 1
    assert after.chapters[1].title == "Chapter 2, Again"
    assert after.outline == before.outline and after.status == "ready"
    others = [c for i, c in enumerate(after.chapters) if i != 1]
    assert others == [c for i, c in enumerate(before.chapters) if i != 1]
    assert app.files.read(f"readings/{book}/book.epub") != epub_before
    assert [r.context.get("kind") for r in seen] == ["revision", None]  # then its new picture
    assert seen[1].context["paragraph"] == after.chapters[1].paragraphs[-1]
    assert after.pictures[2] == "drawn"
    assert f"book revise start book={book} chapter=2" in caplog.text
    assert f"book step book={book} step=revision:2 result=ok" in caplog.text
    assert f"book revised book={book} chapter=2 picture=drawn" in caplog.text
    assert "Make it longer" not in caplog.text and "longer" not in after.model_dump_json()


def test_a_failed_or_stopped_revision_leaves_the_book_as_it_was(application, monkeypatch):
    app = application
    book = ready_book(app)
    before = digests(app, f"readings/{book}")

    async def fail(request, count):
        return AdapterReply(data=None, failure="timeout")

    calls(app, monkeypatch, fail)
    with pytest.raises(books.BookFailed):
        revise(app, book, 2, "Make it longer.")
    assert digests(app, f"readings/{book}") == before
    monkeypatch.undo()

    async def block(request, count):
        if request.media == "image":
            await asyncio.sleep(3600)
        return None

    async def scenario():
        seen = calls(app, monkeypatch, block)
        start_revision(app, LEARNER, book, 3, "Shorter.")
        name = f"reading:{book}"
        run(app, LEARNER, name, lambda: revise_chapter(app, LEARNER, book, 3, "Shorter.", noop))
        while not any(r.media == "image" for r in seen):
            await asyncio.sleep(0.01)
        await stop_books(app)

    asyncio.run(scenario())
    assert digests(app, f"readings/{book}") == before
    (row,) = book_rows(app)
    assert row["state"] == "ready" and row["reason"] == STOPPED


def test_a_failed_new_picture_drops_the_old_one(application, monkeypatch):
    app = application
    book = ready_book(app)

    async def no_picture(request, count):
        return AdapterReply(data=None, failure="unavailable") if request.media else None

    calls(app, monkeypatch, no_picture)
    revise(app, book, 1, "More rain.")
    record = load(app, book)
    assert record.pictures[1] == "failed" and record.revisions == 1
    assert app.files.read(f"readings/{book}/pictures/chapter-1") is None
    assert app.files.read(f"readings/{book}/pictures/chapter-2")[:4] == b"\x89PNG"  # one name
    assert not any(n.startswith("OEBPS/images/c1-") for n in epub_names(app, book))


def test_revision_is_refused_unless_the_book_is_ready_and_the_feedback_is_there(application):
    app = application
    book = ready_book(app)
    with pytest.raises(ValueError):
        start_revision(app, LEARNER, book, 1, "   ")
    for chapter in (0, 5):
        with pytest.raises(ValueError):
            start_revision(app, LEARNER, book, chapter, "More.")
    app.jobs["reading:x"] = None
    with pytest.raises(Conflict):
        start_revision(app, LEARNER, book, 1, "More.")
    del app.jobs["reading:x"]
    books.save(app, load(app, book).model_copy(update={"status": "writing"}))
    with pytest.raises(Conflict):
        start_revision(app, LEARNER, book, 1, "More.")


def test_chinese_feedback_is_data_and_output_stays_english(application, monkeypatch, caplog):
    app = application
    book = ready_book(app)
    adapter = app.models.adapters["simulated"]

    async def chinese_once(request, count):
        if request.context.get("kind") == "revision" and "rejected" not in request.context:
            reply = adapter.respond(request)
            return AdapterReply(data=reply | {"title": "雨夜"})
        return None

    seen = calls(app, monkeypatch, chinese_once)
    with caplog.at_level(logging.INFO, logger="project_j"):
        start_revision(app, LEARNER, book, 2, "把马的那段写长一点")
        revise(app, book, 2, "把马的那段写长一点")
    assert seen[0].context["feedback"] == "把马的那段写长一点"
    assert load(app, book).chapters[1].title == "Chapter 2, Again"
    assert f"book step book={book} step=revision:2 result=repaired" in caplog.text
    assert "马" not in caplog.text


def test_an_unreadable_book_record_is_skipped_not_the_home_page(application, caplog):
    app = application
    book = ready_book(app)
    broken = "lesson-001-2026-10-28-20261120T101500Z"
    app.files.put(f"readings/{broken}/book.json", '{"old": "shape"}')
    with caplog.at_level(logging.WARNING, logger="project_j"):
        rows = book_rows(app)
    assert [row["book_id"] for row in rows] == [book]
    assert f"book unreadable book={broken}" in caplog.text


def test_a_resumed_book_draws_only_the_pictures_not_yet_tried(application, monkeypatch):
    app = application
    course_ref = taken(app)
    book = start_book(app, LEARNER, course_ref, "same")

    async def first_fails_second_waits(request, count):
        drawn = [r for r in seen if r.media == "image"]
        if request.media == "image" and len(drawn) == 1:
            return AdapterReply(data=None, failure="unavailable")
        if request.media == "image" and len(drawn) == 2:
            await asyncio.sleep(3600)
        return None

    async def scenario():
        run(app, LEARNER, f"reading:{book}", lambda: write_book(app, LEARNER, book, noop))
        while len([r for r in seen if r.media == "image"]) < 2:
            await asyncio.sleep(0.01)
        await stop_books(app)

    seen = calls(app, monkeypatch, first_fails_second_waits)
    asyncio.run(scenario())
    assert load(app, book).pictures == {1: "failed", 2: "pending", 3: "pending", 4: "pending"}
    monkeypatch.undo()
    seen = calls(app, monkeypatch)
    retry_book(app, LEARNER, book)
    write(app, book)
    assert len(seen) == 3 and all(r.media == "image" for r in seen)  # chapters 2-4 only
    assert load(app, book).pictures == {1: "failed", 2: "drawn", 3: "drawn", 4: "drawn"}


def test_the_done_log_counts_the_words_the_outline_planned(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    plant_words(app, ["lantern", "pebble", "meadow", "kettle"])
    book = start_book(app, LEARNER, course_ref, "same")

    async def block(request, count):
        if request.context.get("number") == 1:
            await asyncio.sleep(3600)
        return None

    async def scenario():
        seen = calls(app, monkeypatch, block)
        run(app, LEARNER, f"reading:{book}", lambda: write_book(app, LEARNER, book, noop))
        while not any(r.context.get("number") == 1 for r in seen):
            await asyncio.sleep(0.01)
        await stop_books(app)

    asyncio.run(scenario())
    monkeypatch.undo()
    plant_words(app, ["lantern", "pebble", "meadow", "kettle", "anchor", "basket"], key="more")
    assert len(app.vocabulary_record(LEARNER)[1].words) == 6
    retry_book(app, LEARNER, book)
    with caplog.at_level(logging.INFO, logger="project_j"):
        write(app, book)
    assert "offered=4 missing=0" in caplog.text  # the outline's words, not today's box
