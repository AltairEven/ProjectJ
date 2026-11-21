"""Upgrading a run: readings/ becomes books/, and each old book becomes a saved version and/or
a draft once (ADR-0035)."""

import hashlib
import json
import logging
import subprocess
import sys
from types import SimpleNamespace

from project_j.platform.files import PrivateFiles
from project_j.platform.storage import SimulationStore

BOOK = "lesson-001-2026-10-28-20261120T101500Z"
PICTURE = b"\x89PNG\r\n\x1a\nchapter-1"


def old_book(store, record, epub=True, tree="readings"):
    folder = store.run / tree / BOOK
    (folder / "pictures").mkdir(parents=True)
    (folder / "book.json").write_text(json.dumps(record))
    (folder / "pictures/chapter-1").write_bytes(PICTURE)
    if epub:
        (folder / "book.epub").write_bytes(b"PK-epub")


def record(**extra):
    return {
        "status": "ready",
        "proposal": None,
        "proposal_version": 0,
        "pictures": {"1": "drawn"},
        "chapters": [{"title": "One"}],
    } | extra


def read(store, path):
    return json.loads((store.run / f"books/{BOOK}/{path}").read_text())


def opened(tmp_path, setup):
    store = SimulationStore(tmp_path / "simulation", "run")
    setup(store)
    PrivateFiles(store)
    return store


def test_readings_become_books_and_a_finished_book_stays_saved(tmp_path, caplog):
    finished = record()
    with caplog.at_level(logging.INFO, logger="project_j"):
        store = opened(tmp_path, lambda s: old_book(s, finished))
    assert not (store.run / "readings").exists()
    assert read(store, "book.json") == finished
    assert not (store.run / f"books/{BOOK}/draft").exists()
    assert "books migration books=1 drafts=0" in caplog.text


def test_an_unfinished_book_with_an_epub_gets_a_draft(tmp_path):
    writing = record(status="writing", pictures={"1": "drawn", "2": "pending"})
    store = opened(tmp_path, lambda s: old_book(s, writing))
    saved = read(store, "book.json")
    assert saved["status"] == "ready" and saved["pictures"] == {"1": "drawn", "2": "failed"}
    assert saved["proposal"] is None and saved["proposal_version"] == 0
    assert read(store, "draft/book.json") == writing
    assert (store.run / f"books/{BOOK}/draft/pictures/chapter-1").read_bytes() == PICTURE
    assert (store.run / f"books/{BOOK}/pictures/chapter-1").read_bytes() == PICTURE


def test_a_book_discussing_new_chapters_keeps_its_proposal_in_the_draft(tmp_path):
    discussing = record(proposal={"title": "More"}, proposal_version=2)
    store = opened(tmp_path, lambda s: old_book(s, discussing))
    saved, draft = read(store, "book.json"), read(store, "draft/book.json")
    assert saved["proposal"] is None and saved["proposal_version"] == 0
    assert draft["proposal"] == {"title": "More"} and draft["proposal_version"] == 2


def test_a_book_without_an_epub_moves_into_its_draft(tmp_path, caplog):
    outlining = record(status="outlining", pictures={})
    with caplog.at_level(logging.INFO, logger="project_j"):
        store = opened(tmp_path, lambda s: old_book(s, outlining, epub=False))
    assert not (store.run / f"books/{BOOK}/book.json").exists()
    assert not (store.run / f"books/{BOOK}/pictures").exists()
    assert read(store, "draft/book.json") == outlining
    assert (store.run / f"books/{BOOK}/draft/pictures/chapter-1").read_bytes() == PICTURE
    assert "books migration books=1 drafts=1" in caplog.text


def test_both_trees_are_left_alone_with_a_warning(tmp_path, caplog):
    def both(store):
        old_book(store, record(status="writing"))
        (store.run / "books").mkdir()

    with caplog.at_level(logging.INFO, logger="project_j"):
        store = opened(tmp_path, both)
    assert json.loads((store.run / f"readings/{BOOK}/book.json").read_text())["status"] == "writing"
    assert "books migration skipped" in caplog.text


def test_a_broken_record_is_left_as_it_is(tmp_path, caplog):
    def broken(store):
        old_book(store, record())
        (store.run / f"readings/{BOOK}/book.json").write_text("{not json")

    with caplog.at_level(logging.INFO, logger="project_j"):
        store = opened(tmp_path, broken)
    assert (store.run / f"books/{BOOK}/book.json").read_text() == "{not json"
    assert f"book unreadable book={BOOK}" in caplog.text


def test_migration_happens_once(tmp_path, caplog):
    store = opened(tmp_path, lambda s: old_book(s, record(status="writing")))

    def files():
        root = store.run / "books"
        return {
            p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
        }

    before = files()
    with caplog.at_level(logging.INFO, logger="project_j"):
        PrivateFiles(store)
    assert files() == before and "books migration" not in caplog.text


def test_the_service_log_shows_the_migration_at_startup(tmp_path):
    """The service configures its log before it opens the run's files, so the line is not lost."""
    root = tmp_path / "simulation"
    old_book(SimpleNamespace(run=root / "run"), record())
    serve = (
        "import sys\nfrom project_j.interface import api\n"
        "api.uvicorn.Server.run = lambda server: setattr(server, 'started', True)\n"
        "api.main(sys.argv[1:])"
    )
    command = [sys.executable, "-c", serve, "--run-id", "run", "--simulation-root", str(root)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert "INFO project_j.platform.files books migration books=1 drafts=0" in done.stderr
