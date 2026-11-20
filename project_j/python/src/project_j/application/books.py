"""Expanded reading: a taken lesson's story written out as an English novel in the student's
readings/ folder, step by step so a stop or a failure resumes where it was (ADR-0033).

A book is files, not records: book.json (the run's state), pictures/ and book.epub. It is a
background job named reading:<book>; a class start cancels it first. The lesson folder, the
profile and the vocabulary box are only read.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from project_j.application.progress import in_class
from project_j.contracts import Contract, RevisionRef
from project_j.learning.onboarding import profile
from project_j.platform.storage import Conflict
from project_j.preparation.difficulty import targets
from project_j.preparation.drafting import repair_request
from project_j.preparation.epub import Page, build_epub
from project_j.preparation.expansion import (
    ChapterDraft,
    Outline,
    chapter_problems,
    chapter_request,
    outline_problems,
    outline_request,
    picture_request,
    revision_request,
)
from project_j.preparation.folders import lesson_of
from project_j.preparation.reading import words as count_words

log = logging.getLogger(__name__)
BOOK_PATTERN = r"^[a-z]+-\d{3}-\d{4}-\d{2}-\d{2}-\d{8}T\d{6}Z$"
JOB = "reading:"
STOPPED = "stopped because a class started"
STILL = ("image/png", "image/jpeg", "image/webp")  # Story pictures a book takes; no SVG.


class BookFailed(Exception):
    """A step the book cannot do without the student retrying; the message says which."""


class BookRecord(Contract):
    book_id: str
    course_ref: RevisionRef
    lesson: str  # The lesson folder the story came from.
    challenge: str
    target: str | None
    status: Literal["writing", "ready"]
    outline: Outline | None = None
    chapters: list[ChapterDraft] = []
    pictures: dict[int, Literal["pending", "drawn", "failed"]] = {}  # Each chapter's new one.
    revisions: int = 0
    calls: list[dict[str, JsonValue]] = []  # Per step: the binding and the checked config.


def folder(book_id: str) -> str:
    return f"readings/{book_id}"


def load(app, book_id: str) -> BookRecord:
    data = app.files.read(f"{folder(book_id)}/book.json")
    if data is None:
        raise LookupError("This book does not exist.")
    return BookRecord.model_validate_json(data)


def save(app, record: BookRecord) -> BookRecord:
    app.files.put(f"{folder(record.book_id)}/book.json", record.model_dump_json(indent=2) + "\n")
    return record


def guard(app):
    if in_class(app):
        raise Conflict("A class is running.")
    if any(name.startswith(JOB) for name in app.jobs):
        raise Conflict("A book is being written; wait until it is done.")
    if "reference" in app.jobs:
        raise Conflict("The reference check is running; wait until it is done.")


def originals(course: dict, lesson: dict) -> list[dict]:
    """The lesson's still story pictures the student saw: id, paragraph, what it shows, file."""
    files = lesson["files"]
    return [
        {
            "picture": item["resource_id"],
            "paragraph": item["paragraph"],
            "content": item["content"],
            "asset": files[item["resource_id"]],
        }
        for item in course["resources"]
        if item.get("stage") == "story"
        and item["kind"] == "image"
        and item["audience"] == "student"
        and files.get(item["resource_id"], {}).get("media_type") in STILL
    ]


def book_targets(app, learner_id: str) -> dict[str, str]:
    current = profile(app, learner_id)
    return targets(current.body.get("lexile") if current else None)


def start_book(app, learner_id: str, course_ref: RevisionRef, challenge: str) -> str:
    """Check and save a new book's first record; the background job then writes it."""
    guard(app)
    app.models.require()
    lesson = lesson_of(app, learner_id, course_ref)
    taught = any(
        RevisionRef.model_validate(fact.body["course_ref"]).object_id == course_ref.object_id
        for fact in app.store.list_current(learner_id, "fact")
    )
    if lesson is None or not taught:
        raise Conflict("Only a lesson you have taken can be expanded.")
    name = lesson.body["folder"].rsplit("/", 1)[1]
    book_id = f"{name}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    if app.files.read(f"{folder(book_id)}/book.json") is not None:
        raise Conflict("A book of this lesson was started a moment ago; try again.")
    target = book_targets(app, learner_id).get(challenge)
    record = BookRecord(
        book_id=book_id,
        course_ref=course_ref,
        lesson=lesson.body["folder"],
        challenge=challenge,
        target=target,
        status="writing",
    )
    save(app, record)
    log.info(
        "book start book=%s lesson=%s challenge=%s target=%s", book_id, name, challenge, target
    )
    return book_id


def retry_book(app, learner_id: str, book_id: str) -> None:
    guard(app)
    app.models.require()
    if load(app, book_id).status != "writing":
        raise Conflict("This book is already done.")


async def ask(app, book_id: str, step: str, request, check):
    """One text call and at most one repair. check(output) -> (problems, missing words): only
    problems fail after the repair. Returns (output, missing words, call entry)."""
    started = time.perf_counter()
    outcome = "ok"
    for attempt in range(2):
        result = await app.models.run(request)
        if result.failure:
            outcome = f"failed failure={result.failure}"
            break
        hard, missing = check(result.output)
        if not hard and (not missing or attempt):
            break
        if attempt:
            outcome = "failed failure=checks"
            break
        unused = [f"use these words: {', '.join(missing)}"] if missing else []
        request = repair_request(request, result.output, hard + unused)
        outcome = "repaired"
    ms = (time.perf_counter() - started) * 1000
    log.info("book step book=%s step=%s result=%s ms=%.0f", book_id, step, outcome, ms)
    if outcome.startswith("failed"):
        reason = result.failure or "did not pass the checks"
        raise BookFailed(f"The {step.replace(':', ' ')} step failed: {reason}.")
    entry = {
        "step": step,
        "binding": result.binding.model_dump() if result.binding else None,
        "actual": {name: check.model_dump() for name, check in result.actual.items()},
    }
    return result.output, missing, entry


async def draw(
    app, book_id: str, outline: Outline, draft: ChapterDraft, number: int
) -> bytes | None:
    started = time.perf_counter()
    result = await app.models.run(picture_request(outline, draft))
    picture = result.media[0] if result.failure is None else None
    log.info(
        "book picture book=%s chapter=%d result=%s ms=%.0f",
        book_id,
        number,
        "drawn" if picture else "failed",
        (time.perf_counter() - started) * 1000,
    )
    return picture


def picture_path(book_id: str, number: int) -> str:
    """A chapter's new picture; no suffix, as the EPUB types pictures by their own header."""
    return f"{folder(book_id)}/pictures/chapter-{number}"


def source(app, learner_id: str, record: BookRecord):
    """The story, its original pictures and its picture style."""
    lesson = lesson_of(app, learner_id, record.course_ref).body
    course = app.store.read(record.course_ref, learner_id).body
    return app.files.read_asset(lesson["story"]), originals(course, lesson), course


def shown(pictures: list[dict]) -> list[dict]:
    return [{key: value for key, value in item.items() if key != "asset"} for item in pictures]


def book_words(record: BookRecord) -> int:
    return sum(len(count_words(p)) for chapter in record.chapters for p in chapter.paragraphs)


def epub(app, record: BookRecord, pictures: list[dict]) -> bytes:
    """The book from its chapters, the original pictures and the new ones drawn on disk."""
    assets = {item["picture"]: item["asset"] for item in pictures}
    pages = []
    for number, chapter in enumerate(record.chapters, start=1):
        placed = {p.paragraph: app.files.read_binary(assets[p.picture]) for p in chapter.pictures}
        if record.pictures.get(number) == "drawn":
            placed[chapter.new_picture_paragraph] = app.files.read(
                picture_path(record.book_id, number)
            )
        pages.append(Page(chapter.title, chapter.paragraphs, placed))
    return build_epub(record.book_id, record.outline.title, pages, datetime.now(UTC))


def chapter_check(record: BookRecord, number: int):
    outline = record.outline
    words = outline.chapters[number - 1].words
    mine = [place.picture for place in outline.pictures if place.chapter == number]
    return lambda draft: chapter_problems(draft, len(outline.chapters), words, mine)


async def write_book(app, learner_id: str, book_id: str, progress) -> None:
    """Outline, then each chapter, then each pending picture, then the EPUB; every step saved."""
    started = time.perf_counter()
    record = load(app, book_id)
    story, pictures, course = source(app, learner_id, record)
    if record.outline is None:
        _, vocabulary = app.vocabulary_record(learner_id)
        active = [entry for entry in vocabulary.words if entry.status == "active"]
        lemmas = [entry.lemma for entry in active]
        words = [{"lemma": e.lemma, "part_of_speech": e.part_of_speech} for e in active]
        request = outline_request(
            story, record.target, words, shown(pictures), course.get("picture_style") or ""
        )
        ids = [item["picture"] for item in pictures]
        outline, _, entry = await ask(
            app, book_id, "outline", request, lambda o: (outline_problems(o, lemmas, ids), [])
        )
        record = save(
            app,
            record.model_copy(
                update={
                    "outline": outline,
                    "pictures": dict.fromkeys(range(1, len(outline.chapters) + 1), "pending"),
                    "calls": [*record.calls, entry],
                }
            ),
        )
    planned = len(record.outline.chapters)
    total = 2 * planned + 2

    def done() -> int:
        return 1 + len(record.chapters) + sum(s != "pending" for s in record.pictures.values())

    progress(done(), total)
    for number in range(len(record.chapters) + 1, planned + 1):
        request = chapter_request(
            record.outline, story, record.chapters, number, record.target, shown(pictures)
        )
        draft, _, entry = await ask(
            app, book_id, f"chapter:{number}", request, chapter_check(record, number)
        )
        update = {"chapters": [*record.chapters, draft], "calls": [*record.calls, entry]}
        record = save(app, record.model_copy(update=update))
        progress(done(), total)
    for number, state in record.pictures.items():
        if state != "pending":
            continue
        picture = await draw(app, book_id, record.outline, record.chapters[number - 1], number)
        if picture:
            app.files.put(picture_path(book_id, number), picture)
        pictures_now = record.pictures | {number: "drawn" if picture else "failed"}
        record = save(app, record.model_copy(update={"pictures": pictures_now}))
        progress(done(), total)
    data = epub(app, record, pictures)
    app.files.put(f"{folder(book_id)}/book.epub", data)
    record = save(app, record.model_copy(update={"status": "ready"}))
    missing = sum(
        len(chapter_check(record, n)(chapter)[1]) for n, chapter in enumerate(record.chapters, 1)
    )
    states = list(record.pictures.values())
    # Offered: the words the outline spread over the chapters (every active word at its run).
    offered = {word.lower() for chapter in record.outline.chapters for word in chapter.words}
    log.info(
        "book done book=%s chapters=%d words=%d offered=%d missing=%d drawn=%d failed=%d "
        "epub_bytes=%d ms=%.0f",
        book_id,
        planned,
        book_words(record),
        len(offered),
        missing,
        states.count("drawn"),
        states.count("failed"),
        len(data),
        (time.perf_counter() - started) * 1000,
    )


def books(app) -> list[dict]:
    """One row per book in readings/, as the course page shows it."""
    rows = []
    for book_id in app.files.folders("readings"):
        if not re.fullmatch(BOOK_PATTERN, book_id):
            continue
        try:
            record = load(app, book_id)
        except (LookupError, ValueError) as error:  # One broken book must not stop the page.
            log.warning("book unreadable book=%s error=%s", book_id, type(error).__name__)
            continue
        name = JOB + book_id
        running = name in app.jobs
        planned = len(record.outline.chapters) if record.outline else None
        reason = app.job_failures.get(name)
        if record.status == "ready":
            state = "revising" if running else "ready"
        elif running:
            state = "writing" if planned is None or len(record.chapters) < planned else "drawing"
        else:
            state, reason = "failed", reason or "stopped before it finished"
        rows.append(
            {
                "book_id": book_id,
                "course_ref": record.course_ref.model_dump(),
                "title": record.outline.title if record.outline else None,
                "target": record.target,
                "words": book_words(record),
                "chapters": [chapter.title for chapter in record.chapters],
                "planned": planned,
                "state": state,
                "reason": None if running else reason,
            }
        )
    return rows


def start_revision(app, learner_id: str, book_id: str, chapter: int, feedback: str) -> None:
    if not feedback.strip():
        raise ValueError("Write what should change in this chapter.")
    guard(app)
    app.models.require()
    record = load(app, book_id)
    if record.status != "ready":
        raise Conflict("Only a finished book can be revised.")
    if not 1 <= chapter <= len(record.chapters):
        raise ValueError("This book has no such chapter.")
    log.info("book revise start book=%s chapter=%d", book_id, chapter)


async def revise_chapter(
    app, learner_id: str, book_id: str, chapter: int, feedback: str, progress
) -> None:
    """Rewrite one chapter by the student's feedback and draw its new picture, all in memory;
    then write the picture, the EPUB and the record with no wait between, so a stop or a failure
    before that leaves the book as it was. The feedback is used once and kept nowhere."""
    started = time.perf_counter()
    record = load(app, book_id)
    story, pictures, _ = source(app, learner_id, record)
    progress(0, 2)
    request = revision_request(
        record.outline, story, record.chapters, chapter, feedback, record.target, shown(pictures)
    )
    draft, _, entry = await ask(
        app, book_id, f"revision:{chapter}", request, chapter_check(record, chapter)
    )
    progress(1, 2)
    picture = await draw(app, book_id, record.outline, draft, chapter)
    chapters = [draft if n == chapter else c for n, c in enumerate(record.chapters, start=1)]
    record = record.model_copy(
        update={
            "chapters": chapters,
            "pictures": record.pictures | {chapter: "drawn" if picture else "failed"},
            "revisions": record.revisions + 1,
            "calls": [*record.calls, entry],
        }
    )
    if picture:
        app.files.put(picture_path(book_id, chapter), picture)
    else:  # The old picture may not fit the new text.
        app.files.remove(picture_path(book_id, chapter))
    data = epub(app, record, pictures)
    app.files.put(f"{folder(book_id)}/book.epub", data)
    save(app, record)
    log.info(
        "book revised book=%s chapter=%d picture=%s epub_bytes=%d ms=%.0f",
        book_id,
        chapter,
        "drawn" if picture else "failed",
        len(data),
        (time.perf_counter() - started) * 1000,
    )


async def stop_books(app) -> None:
    """Before a class opens: cancel a book being written and wait until it has stopped."""
    for name, task in list(app.jobs.items()):
        if name.startswith(JOB):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            app.job_failures[name] = STOPPED
            log.info("book stopped book=%s reason=class", name.removeprefix(JOB))


def book_file(app, book_id: str) -> bytes:
    if not re.fullmatch(BOOK_PATTERN, book_id):
        raise ValueError("not a book")
    data = app.files.read(f"{folder(book_id)}/book.epub")
    if data is None:
        raise LookupError("This book has no EPUB yet.")
    log.info("book download book=%s bytes=%d", book_id, len(data))
    return data
