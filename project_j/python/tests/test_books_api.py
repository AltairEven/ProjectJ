"""Expanded reading through the routes: expand, follow, download, retry, revise; a class start
stops a book first."""

import asyncio
import logging

from fastapi.testclient import TestClient
from support import LEARNER, settle
from test_books import calls, load, noop, taken

from project_j.application.books import STOPPED, start_book, write_book
from project_j.application.configuration import Settings
from project_j.application.progress import run
from project_j.interface.api import create_app


def headers(app):
    return {"Authorization": f"Bearer {app.token}"}


def test_expand_runs_in_the_background_and_downloads(application, caplog):
    app = application
    course_ref = taken(app)
    with TestClient(create_app(Settings(), app)) as client:
        body = {"learner_id": LEARNER, "course_ref": course_ref.model_dump(), "challenge": "little"}
        reply = client.post("/api/books/expand", json=body, headers=headers(app))
        assert reply.status_code == 202
        book = reply.json()["book_id"]
        assert reply.json()["job"] == f"reading:{book}"
        settle(app)
        dashboard = client.get("/api/dashboard", headers=headers(app)).json()
        (row,) = dashboard["books"]
        assert row["state"] == "ready" and row["book_id"] == book
        assert row["course_ref"] == course_ref.model_dump() and row["target"] == "550L"
        assert dashboard["book_targets"]["harder"] == "650L"
        with caplog.at_level(logging.INFO, logger="project_j"):
            file = client.get(f"/api/books/file?book_id={book}", headers=headers(app))
        assert file.status_code == 200 and file.content[:2] == b"PK"
        assert file.headers["content-type"] == "application/epub+zip"
        assert f'filename="{book}.epub"' in file.headers["content-disposition"]
        assert file.headers["cache-control"] == "no-store"
        assert f"book download book={book} bytes={len(file.content)}" in caplog.text
        revise = {"learner_id": LEARNER, "book_id": book, "chapter": 2, "feedback": "Longer."}
        reply = client.post("/api/books/revise", json=revise, headers=headers(app))
        assert reply.status_code == 202 and reply.json()["job"] == f"reading:{book}"
        settle(app)
        assert load(app, book).revisions == 1
        retry = client.post(
            "/api/books/retry", json={"learner_id": LEARNER, "book_id": book}, headers=headers(app)
        )
        assert retry.status_code == 409  # a finished book has nothing to retry


def test_a_running_book_holds_the_job_lane_and_a_second_waits(application, monkeypatch):
    app = application
    course_ref = taken(app)
    app.models.adapters["simulated"].delay_seconds = 0.05
    with TestClient(create_app(Settings(), app)) as client:
        body = {"learner_id": LEARNER, "course_ref": course_ref.model_dump()}
        first = client.post("/api/books/expand", json=body, headers=headers(app))
        # Settings and the reference check refuse while any job runs; a book is one of them.
        assert first.json()["job"] in app.jobs
        second = client.post("/api/books/expand", json=body, headers=headers(app))
        assert second.status_code == 409
        (row,) = client.get("/api/dashboard", headers=headers(app)).json()["books"]
        assert row["state"] in {"writing", "drawing"} and row["reason"] is None
        settle(app)


def test_starting_a_class_stops_the_book_first(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    book = start_book(app, LEARNER, course_ref, "same")
    opened = []
    start = app.start

    def watched_start(*args, **kwargs):
        opened.append([name for name in app.jobs if name.startswith("reading:")])
        return start(*args, **kwargs)

    monkeypatch.setattr(app, "start", watched_start)

    async def block(request, count):
        if request.context.get("number") == 2:
            await asyncio.sleep(3600)
        return None

    async def scenario():
        seen = calls(app, monkeypatch, block)
        run(app, LEARNER, f"reading:{book}", lambda: write_book(app, LEARNER, book, noop))
        while not any(r.context.get("number") == 2 for r in seen):
            await asyncio.sleep(0.01)
        await app.confirm_and_start("start2", LEARNER, course_ref, True)

    with caplog.at_level(logging.INFO, logger="project_j"):
        asyncio.run(scenario())
    assert opened == [[]]  # the book had stopped before the class opened
    assert app.job_failures[f"reading:{book}"] == STOPPED
    assert app.store.active_activity_id  # the class is open
    assert f"book stopped book={book} reason=class" in caplog.text


def test_book_routes_need_the_token_and_valid_input(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        assert client.get("/api/books/file?book_id=x").status_code == 401
        bad = client.get("/api/books/file?book_id=../x", headers=headers(app))
        assert bad.status_code == 422
        book = "lesson-001-2026-10-28-20261120T101500Z"
        assert (
            client.get(f"/api/books/file?book_id={book}", headers=headers(app)).status_code == 404
        )
        for revise in (
            {"learner_id": LEARNER, "book_id": book, "chapter": 1, "feedback": ""},
            {"learner_id": LEARNER, "book_id": book, "chapter": 7, "feedback": "x"},
            {"learner_id": LEARNER, "book_id": "../x", "chapter": 1, "feedback": "x"},
            {"learner_id": LEARNER, "book_id": book, "chapter": 1, "feedback": "x" * 2001},
        ):
            reply = client.post("/api/books/revise", json=revise, headers=headers(app))
            assert reply.status_code == 422
        expand = {"learner_id": LEARNER, "course_ref": {"object_id": "c", "revision": 1}}
        assert (
            client.post(
                "/api/books/expand", json=expand | {"challenge": "x"}, headers=headers(app)
            ).status_code
            == 422
        )
