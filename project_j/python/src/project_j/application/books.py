"""Expanded reading: a taken lesson's story written out as an English novel in the student's
books/ folder, step by step so a stop or a failure resumes where it was (ADR-0033).

A book is files, not records: books/<book>/ holds the saved book (book.json, pictures/ and
book.epub, which reading and downloading use) and draft/ the one being edited (its book.json and
pictures/). Every edit works on the draft; only a save packs it and replaces the saved book, and
discarding the changes removes the draft (ADR-0035). The student and Jane first agree an outline
in rounds; only a confirmed outline is written, and a finished book grows by new chapters agreed
the same way (ADR-0034). Each step is a background job named reading:<book>; a class start
cancels it first. The lesson folder, the profile and the vocabulary box are only read.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue, model_validator

from project_j.application.progress import in_class
from project_j.contracts import Contract, RevisionRef
from project_j.learning.onboarding import profile
from project_j.platform.storage import Conflict
from project_j.platform.visuals import image_type
from project_j.preparation.difficulty import targets
from project_j.preparation.drafting import repair_request
from project_j.preparation.epub import Page, build_epub
from project_j.preparation.expansion import (
    ChapterDraft,
    Outline,
    chapter_problems,
    chapter_request,
    extended,
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
    status: Literal["outlining", "writing", "ready"]
    outline: Outline | None = None  # The confirmed outline the chapters follow.
    proposal: Outline | None = None  # Under discussion: a new book's, or a finished one's next.
    proposal_version: int = 0
    chapters: list[ChapterDraft] = []
    pictures: dict[int, Literal["pending", "drawn", "failed"]] = {}  # Each chapter's new one.
    revisions: int = 0
    calls: list[dict[str, JsonValue]] = []  # Per step: the binding and the checked config.

    @model_validator(mode="before")
    @classmethod
    def first_version(cls, data):
        """A first-version book stopped before its outline now waits for one to be agreed."""
        if isinstance(data, dict) and data.get("status") == "writing" and not data.get("outline"):
            return data | {"status": "outlining"}
        return data


def folder(book_id: str, draft: bool = True) -> str:
    """The book's draft, or (draft=False) its saved version."""
    return f"books/{book_id}/draft" if draft else f"books/{book_id}"


def stored(app, book_id: str, draft: bool = True) -> BookRecord | None:
    data = app.files.read(f"{folder(book_id, draft)}/book.json")
    return None if data is None else BookRecord.model_validate_json(data)


def load(app, book_id: str, draft: bool = True) -> BookRecord:
    if (record := stored(app, book_id, draft)) is None:
        raise LookupError("This book does not exist.")
    return record


def save(app, record: BookRecord) -> BookRecord:
    """Every edit writes the draft; the saved book.json is written only by save_book."""
    app.files.put(f"{folder(record.book_id)}/book.json", record.model_dump_json(indent=2) + "\n")
    return record


def has_draft(app, book_id: str) -> bool:
    return app.files.read(f"{folder(book_id)}/book.json") is not None


def current(app, book_id: str) -> BookRecord:
    """The draft if there is one, else the saved book."""
    return stored(app, book_id) or load(app, book_id, draft=False)


def checked(book_id: str) -> str:
    if not re.fullmatch(BOOK_PATTERN, book_id):
        raise ValueError("not a book")
    return book_id


def editable(app, book_id: str) -> None:
    """Before the first edit of a saved book: its record and drawn pictures become the draft,
    the pictures first so an interruption never leaves a draft without them."""
    if has_draft(app, book_id):
        return
    record = load(app, book_id, draft=False)
    for number, state in record.pictures.items():
        if state == "drawn" and (data := app.files.read(picture_path(book_id, number, False))):
            app.files.put(picture_path(book_id, number), data)
    save(app, record)
    log.info("book draft book=%s from=saved", book_id)


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
    """Check and save a new book's first record; the background job then plans its outline."""
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
    if app.files.path(f"books/{book_id}").exists():
        raise Conflict("A book of this lesson was started a moment ago; try again.")
    target = book_targets(app, learner_id).get(challenge)
    record = BookRecord(
        book_id=book_id,
        course_ref=course_ref,
        lesson=lesson.body["folder"],
        challenge=challenge,
        target=target,
        status="outlining",
    )
    save(app, record)
    log.info(
        "book start book=%s lesson=%s challenge=%s target=%s", book_id, name, challenge, target
    )
    log.info("book draft book=%s from=new", book_id)
    return book_id


def retry_book(app, learner_id: str, book_id: str) -> Literal["outlining", "writing"]:
    """A book stopped while being written, or before its first outline: which job resumes it."""
    guard(app)
    app.models.require()
    record = current(app, book_id)
    if record.status == "writing" or (record.status == "outlining" and record.proposal is None):
        return record.status
    raise Conflict("This book has nothing to retry.")


def start_outline_round(app, learner_id: str, book_id: str, feedback: str) -> None:
    """Check a round on the outline under discussion, or (finished book) on new chapters."""
    if not feedback.strip():
        raise ValueError("Write what should change in the outline.")
    guard(app)
    app.models.require()
    record = current(app, book_id)
    if not (record.status == "ready" or (record.status == "outlining" and record.proposal)):
        raise Conflict("This book has no outline to discuss now.")
    editable(app, book_id)
    log.info(
        "book outline start book=%s version=%d continuing=%s",
        book_id,
        record.proposal_version,
        record.status == "ready",
    )


async def plan_outline(app, learner_id: str, book_id: str, feedback: str | None, progress) -> None:
    """One round: the first outline, or the one under discussion (else the confirmed one) redone
    by the feedback; after confirmed chapters only new ones are added. A failed round keeps the
    last outline. The feedback is used once and kept nowhere."""
    record = load(app, book_id)
    story, pictures, course = source(app, learner_id, record)
    _, vocabulary = app.vocabulary_record(learner_id)
    active = [entry for entry in vocabulary.words if entry.status == "active"]
    lemmas = [entry.lemma for entry in active]
    words = [{"lemma": e.lemma, "part_of_speech": e.part_of_speech} for e in active]
    confirmed = record.outline
    kept = len(confirmed.chapters) if confirmed else 0
    request = outline_request(
        story,
        record.target,
        words,
        shown(pictures),
        course.get("picture_style") or "",
        current=record.proposal or confirmed,
        feedback=feedback,
        fixed=kept,
    )
    ids = [item["picture"] for item in pictures]

    def check(output: Outline):
        return outline_problems(extended(output, confirmed), lemmas, ids, kept), []

    output, entry = await ask(app, book_id, "outline", request, check)
    outline = extended(output, confirmed)
    version = record.proposal_version + 1
    update = {"proposal": outline, "proposal_version": version, "calls": [*record.calls, entry]}
    save(app, record.model_copy(update=update))
    log.info(
        "book outline book=%s version=%d chapters=%d continuing=%s",
        book_id,
        version,
        len(outline.chapters),
        confirmed is not None,
    )


def confirm_outline(app, learner_id: str, book_id: str) -> None:
    """The outline under discussion becomes the book's; its chapters not yet written are next."""
    guard(app)
    app.models.require()
    record = current(app, book_id)
    if record.proposal is None:
        raise Conflict("There is no outline to confirm.")
    new = range(len(record.chapters) + 1, len(record.proposal.chapters) + 1)
    update = {
        "outline": record.proposal,
        "proposal": None,
        "proposal_version": 0,
        "status": "writing",
        "pictures": record.pictures | dict.fromkeys(new, "pending"),
    }
    save(app, record.model_copy(update=update))
    log.info(
        "book confirm book=%s chapters=%d new=%d", book_id, len(record.proposal.chapters), len(new)
    )


def discard_proposal(app, book_id: str) -> None:
    """Drop the new chapters under discussion from the draft; its other changes stay."""
    guard(app)
    record = current(app, book_id)
    if record.status != "ready" or record.proposal is None:
        raise Conflict("There are no new chapters to discard.")
    save(app, record.model_copy(update={"proposal": None, "proposal_version": 0}))
    app.job_failures.pop(JOB + book_id, None)  # A failed round of what is now discarded.
    log.info("book discard book=%s", book_id)


def start_drawing(app, learner_id: str, book_id: str) -> int:
    """Mark a finished book's failed pictures to be drawn again; the writing job draws them.
    Its book done log then counts what was drawn and what failed again."""
    guard(app)
    app.models.require()
    record = current(app, book_id)
    failed = [number for number, state in record.pictures.items() if state == "failed"]
    if record.status != "ready" or not failed:
        raise Conflict("This book has no missing pictures to draw.")
    editable(app, book_id)
    update = {"status": "writing", "pictures": record.pictures | dict.fromkeys(failed, "pending")}
    save(app, record.model_copy(update=update))
    log.info("book redraw start book=%s pictures=%d", book_id, len(failed))
    return len(failed)


async def ask(app, book_id: str, step: str, request, check):
    """One text call and at most one repair. check(output) -> (problems, missing words): only
    problems fail. A first draft short only of words stays when its repair fails or comes back
    with problems, since missing words alone never fail. Returns (output, call entry)."""
    started = time.perf_counter()
    outcome, first = "ok", None
    for attempt in range(2):
        result = await app.models.run(request)
        hard, missing = check(result.output) if result.failure is None else ([], [])
        if result.failure is None and not hard and (not missing or attempt):
            break
        if attempt or result.failure:
            if first is not None:
                result, outcome = first, "kept_first"
            else:
                outcome = f"failed failure={result.failure or 'checks'}"
            break
        first = None if hard else result
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
        "actual": {name: item.model_dump() for name, item in result.actual.items()},
    }
    return result.output, entry


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


def picture_path(book_id: str, number: int, draft: bool = True) -> str:
    """A chapter's new picture; no suffix, as the EPUB types pictures by their own header."""
    return f"{folder(book_id, draft)}/pictures/chapter-{number}"


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
    """The draft as a book: its chapters, the original pictures and its new ones drawn."""
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
    return lambda draft: chapter_problems(draft, words, mine)


async def write_book(app, learner_id: str, book_id: str, progress) -> None:
    """The confirmed outline's chapters not yet written, then each pending picture, every step
    saved to the draft; a save packs the EPUB."""
    started = time.perf_counter()
    record = load(app, book_id)
    story, pictures, _ = source(app, learner_id, record)
    planned = len(record.outline.chapters)
    total = 2 * planned + 1

    def done() -> int:
        return 1 + len(record.chapters) + sum(s != "pending" for s in record.pictures.values())

    progress(done(), total)
    for number in range(len(record.chapters) + 1, planned + 1):
        request = chapter_request(
            record.outline, story, record.chapters, number, record.target, shown(pictures)
        )
        draft, entry = await ask(
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
    record = save(app, record.model_copy(update={"status": "ready"}))
    missing = sum(
        len(chapter_check(record, n)(chapter)[1]) for n, chapter in enumerate(record.chapters, 1)
    )
    states = list(record.pictures.values())
    # Offered: the words the outline spread over the chapters (every active word at its run).
    offered = {word.lower() for chapter in record.outline.chapters for word in chapter.words}
    log.info(
        "book done book=%s chapters=%d words=%d offered=%d missing=%d drawn=%d failed=%d ms=%.0f",
        book_id,
        planned,
        book_words(record),
        len(offered),
        missing,
        states.count("drawn"),
        states.count("failed"),
        (time.perf_counter() - started) * 1000,
    )


def changes(saved: BookRecord, draft: BookRecord) -> dict:
    """What the draft changed: the chapters rewritten, how many were added, new pictures drawn."""
    kept = len(saved.chapters)
    pairs = zip(saved.chapters, draft.chapters, strict=False)  # A draft never has fewer.
    rewritten = [n for n, (old, new) in enumerate(pairs, start=1) if old != new]
    drawn = sum(
        state == "drawn" and (n in rewritten or n > kept or saved.pictures.get(n) != "drawn")
        for n, state in draft.pictures.items()
    )
    return {"rewritten": rewritten, "added": len(draft.chapters) - kept, "drawn": drawn}


def books(app) -> list[dict]:
    """One row per book in books/, as the bookshelf shows it: what it is doing from the draft
    when there is one, and the saved book's title, words and chapters."""
    rows = []
    for book_id in app.files.folders("books"):
        if not re.fullmatch(BOOK_PATTERN, book_id):
            continue
        try:
            kept, draft = stored(app, book_id, draft=False), stored(app, book_id)
        except ValueError as error:  # One broken book must not stop the page.
            log.warning("book unreadable book=%s error=%s", book_id, type(error).__name__)
            continue
        if (record := draft or kept) is None:  # A folder with no book in it.
            continue
        name = JOB + book_id
        running = name in app.jobs
        planned = len(record.outline.chapters) if record.outline else None
        reason = app.job_failures.get(name)
        if record.status == "outlining" and (running or record.proposal):
            state = "outlining"  # Waiting for the student's next round or confirmation.
        elif record.status == "ready":
            state = "revising" if book_id in app.book_revising else "ready"
        elif running:
            state = "writing" if len(record.chapters) < planned else "drawing"
        else:
            state, reason = "failed", reason or "stopped before it finished"
        discussed = record.proposal or record.outline
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
                "revising": app.book_revising.get(book_id) if running else None,
                "reason": None if running else reason,
                "outline": discussed.model_dump(mode="json") if discussed else None,
                "version": record.proposal_version if record.proposal else None,
                "continuing": record.status == "ready" and record.proposal is not None,
                "missing_pictures": list(record.pictures.values()).count("failed"),
                "saved": {
                    "title": kept.outline.title,
                    "words": book_words(kept),
                    "chapters": len(kept.chapters),
                }
                if kept
                else None,
                "draft": draft is not None,
                "changes": changes(kept, draft) if kept and draft else None,
            }
        )
    return rows


def start_revision(app, learner_id: str, book_id: str, chapter: int, feedback: str) -> None:
    if not feedback.strip():
        raise ValueError("Write what should change in this chapter.")
    guard(app)
    app.models.require()
    record = current(app, book_id)
    if record.status != "ready":
        raise Conflict("Only a finished book can be revised.")
    if not 1 <= chapter <= len(record.chapters):
        raise ValueError("This book has no such chapter.")
    editable(app, book_id)
    log.info("book revise start book=%s chapter=%d", book_id, chapter)


async def revise_chapter(
    app, learner_id: str, book_id: str, chapter: int, feedback: str, progress
) -> None:
    """The revision job: while it runs, the bookshelf names the chapter being rewritten."""
    app.book_revising[book_id] = chapter
    try:
        await rewrite(app, learner_id, book_id, chapter, feedback, progress)
    finally:
        app.book_revising.pop(book_id, None)


async def rewrite(
    app, learner_id: str, book_id: str, chapter: int, feedback: str, progress
) -> None:
    """Rewrite one chapter by the student's feedback and draw its new picture, all in memory;
    then write the draft's picture and record with no wait between, so a stop or a failure
    before that leaves the draft as it was. The feedback is used once and kept nowhere."""
    started = time.perf_counter()
    record = load(app, book_id)
    story, pictures, _ = source(app, learner_id, record)
    progress(0, 2)
    request = revision_request(
        record.outline, story, record.chapters, chapter, feedback, record.target, shown(pictures)
    )
    draft, entry = await ask(
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
    save(app, record)
    log.info(
        "book revised book=%s chapter=%d picture=%s ms=%.0f",
        book_id,
        chapter,
        "drawn" if picture else "failed",
        (time.perf_counter() - started) * 1000,
    )


async def stop_books(app) -> None:
    """Before a class opens: cancel a book being written and wait until it has stopped."""
    for name, task in list(app.jobs.items()):
        if name.startswith(JOB):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            book_id = name.removeprefix(JOB)
            if app.files.path(folder(book_id, False)).is_dir():  # Not deleted meanwhile.
                app.job_failures[name] = STOPPED
            log.info("book stopped book=%s reason=class", book_id)


def save_book(app, learner_id: str, book_id: str) -> None:
    """Pack the finished draft and replace the saved book: its pictures, then the EPUB, then the
    record, then the draft goes. An error midway may leave new and old parts mixed; the draft
    stays, so saving again completes it."""
    guard(app)
    started = time.perf_counter()
    if (record := stored(app, book_id)) is None:
        raise Conflict("There are no changes to save.")
    if record.proposal is not None:
        raise Conflict("Write or discard the new chapters first")
    if record.status != "ready":
        raise Conflict("Wait until it is written")
    # A picture whose file is gone counts as failed, so it can be drawn again.
    states = {
        n: "failed"
        if s == "drawn" and not app.files.path(picture_path(book_id, n)).is_file()
        else s
        for n, s in record.pictures.items()
    }
    record = record.model_copy(update={"pictures": states})
    _, pictures, _ = source(app, learner_id, record)
    data = epub(app, record, pictures)
    for number in range(1, len(record.chapters) + 1):
        if record.pictures.get(number) == "drawn":
            app.files.put(
                picture_path(book_id, number, False), app.files.read(picture_path(book_id, number))
            )
        else:
            app.files.remove(picture_path(book_id, number, False))
    app.files.put(f"{folder(book_id, False)}/book.epub", data)
    app.files.put(f"{folder(book_id, False)}/book.json", record.model_dump_json(indent=2) + "\n")
    app.files.remove_tree(folder(book_id))
    log.info(
        "book save book=%s chapters=%d pictures=%d epub_bytes=%d ms=%.0f",
        book_id,
        len(record.chapters),
        list(record.pictures.values()).count("drawn"),
        len(data),
        (time.perf_counter() - started) * 1000,
    )


def revert_book(app, book_id: str) -> None:
    """Discard the changes: remove the whole draft, new chapters under discussion too."""
    guard(app)
    if app.files.read(f"{folder(book_id, False)}/book.json") is None:
        raise Conflict("A book never saved has nothing to go back to.")
    if not has_draft(app, book_id):
        raise Conflict("There are no changes to discard.")
    app.files.remove_tree(folder(book_id))
    app.job_failures.pop(JOB + book_id, None)  # A failed step of the draft now gone.
    log.info("book revert book=%s", book_id)


async def delete_book(app, book_id: str) -> None:
    """Delete the whole book, saved version and draft alike, for good; a job still working on it
    is stopped first. Another book's job and the reference check go on. The records go last, so
    an error midway leaves the book on the shelf, its records kept, to be deleted again."""
    root, draft_folder = folder(checked(book_id), False), folder(book_id)
    kept = app.files.read(f"{root}/book.json") is not None  # 423 in class
    draft = has_draft(app, book_id)
    if not (kept or draft):
        raise LookupError("This book does not exist.")
    name = JOB + book_id
    task = app.jobs.get(name)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    app.files.remove_tree(f"{draft_folder}/pictures")
    app.files.remove_tree(f"{root}/pictures")
    for path in (f"{root}/book.epub", f"{draft_folder}/book.json", f"{root}/book.json"):
        app.files.remove(path)
    app.files.remove_tree(root)  # Its emptied folders and anything else left in it.
    app.job_failures.pop(name, None)
    log.info(
        "book delete book=%s saved=%s draft=%s stopped=%s",
        book_id,
        "yes" if kept else "no",
        "yes" if draft else "no",
        "yes" if task else "no",
    )


def book_content(app, book_id: str, draft: bool) -> dict:
    """The saved book or the draft as the reader shows it: each written chapter's paragraphs and
    its pictures by paragraph, an original by its id and the chapter's new one as None."""
    record = load(app, checked(book_id), draft)
    chapters = []
    for number, chapter in enumerate(record.chapters, start=1):
        places = [{"paragraph": p.paragraph, "picture": p.picture} for p in chapter.pictures]
        if record.pictures.get(number) == "drawn":
            places.append({"paragraph": chapter.new_picture_paragraph, "picture": None})
        places.sort(key=lambda place: place["paragraph"])
        chapters.append(
            {"title": chapter.title, "paragraphs": chapter.paragraphs, "pictures": places}
        )
    version = "draft" if draft else "saved"
    log.info("book read book=%s version=%s chapters=%d", book_id, version, len(chapters))
    return {"title": record.outline.title if record.outline else None, "chapters": chapters}


def book_picture(
    app, learner_id: str, book_id: str, draft: bool, chapter: int | None, picture: str | None
) -> tuple[bytes, str]:
    """One picture, looked up only in the book's own list: a chapter's new one by the chapter,
    an original by the id a written chapter places. The request never names a path."""
    checked(book_id)
    if (chapter is None) == (picture is None):
        raise ValueError("Give a chapter or a picture.")
    record = load(app, book_id, draft)
    data = None
    if chapter is not None:
        if 1 <= chapter <= len(record.chapters) and record.pictures.get(chapter) == "drawn":
            data = app.files.read(picture_path(book_id, chapter, draft))
    elif any(place.picture == picture for c in record.chapters for place in c.pictures):
        _, pictures, _ = source(app, learner_id, record)
        asset = next((item["asset"] for item in pictures if item["picture"] == picture), None)
        data = app.files.read_binary(asset) if asset else None
    if data is None:
        raise LookupError("This book has no such picture.")
    return data, f"image/{image_type(data)}"


def book_file(app, book_id: str) -> bytes:
    data = app.files.read(f"{folder(checked(book_id), False)}/book.epub")
    if data is None:
        raise LookupError("This book has no EPUB yet.")
    log.info("book download book=%s bytes=%d", book_id, len(data))
    return data
