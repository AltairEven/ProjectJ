"""Expanded reading through the routes: expand, agree the outline, follow, download, retry,
revise, continue, draw missing pictures; a class start stops a book first."""

import asyncio
import logging

from fastapi.testclient import TestClient
from support import LEARNER, settle
from test_books import calls, confirmed, digests, load, noop, taken, write

from project_j.application.books import STOPPED, write_book
from project_j.application.configuration import Settings
from project_j.application.progress import run
from project_j.interface.api import create_app
from project_j.platform.models.contracts import AdapterReply


def headers(app):
    return {"Authorization": f"Bearer {app.token}"}


def post(client, app, route, body):
    return client.post(
        f"/api/books/{route}", json={"learner_id": LEARNER} | body, headers=headers(app)
    )


def row_of(client, app):
    (row,) = client.get("/api/dashboard", headers=headers(app)).json()["books"]
    return row


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
        post(client, app, "confirm", {"book_id": book})
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
        assert row["state"] == "outlining" and row["reason"] is None
        settle(app)


def test_starting_a_class_stops_the_book_first(application, monkeypatch, caplog):
    app = application
    course_ref = taken(app)
    book = confirmed(app, course_ref)
    opened, files = [], []
    start = app.start

    def watched_start(*args, **kwargs):
        opened.append([name for name in app.jobs if name.startswith("reading:")])
        files.append(digests(app, f"readings/{book}"))
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
    assert files == [digests(app, f"readings/{book}")]  # and wrote nothing after
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
            {"learner_id": LEARNER, "book_id": book, "chapter": 13, "feedback": "x"},
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


def test_expand_discuss_confirm_and_continue_through_the_routes(application):
    app = application
    course_ref = taken(app)
    with TestClient(create_app(Settings(), app)) as client:
        reply = post(client, app, "expand", {"course_ref": course_ref.model_dump()})
        book = reply.json()["book_id"]
        settle(app)
        row = row_of(client, app)
        assert row["state"] == "outlining" and row["version"] == 1
        assert row["outline"]["title"] == "The Longer Story" and row["downloadable"] is False
        reply = post(client, app, "outline", {"book_id": book, "feedback": "Add a fox."})
        assert reply.status_code == 202 and reply.json()["job"] == f"reading:{book}"
        settle(app)
        assert row_of(client, app)["version"] == 2
        assert post(client, app, "confirm", {"book_id": book}).status_code == 202
        settle(app)
        row = row_of(client, app)
        assert row["state"] == "ready" and row["downloadable"] is True
        assert (
            client.get(f"/api/books/file?book_id={book}", headers=headers(app)).status_code == 200
        )
        post(client, app, "outline", {"book_id": book, "feedback": "Go on."})
        settle(app)
        row = row_of(client, app)
        assert row["continuing"] is True and len(row["outline"]["chapters"]) == 5
        reply = post(client, app, "discard", {"book_id": book})
        assert reply.status_code == 200 and reply.json() == {"book_id": book}
        assert row_of(client, app)["continuing"] is False
        post(client, app, "outline", {"book_id": book, "feedback": "Go on."})
        settle(app)
        post(client, app, "confirm", {"book_id": book})
        settle(app)
        row = row_of(client, app)
        assert row["state"] == "ready" and len(row["chapters"]) == 5


def test_retry_route_picks_the_job_the_book_needs(application, monkeypatch):
    app = application
    course_ref = taken(app)

    async def fail_outline(request, count):
        if request.context.get("kind") == "outline":
            return AdapterReply(data=None, failure="unavailable")
        return None

    seen = calls(app, monkeypatch, fail_outline)
    with TestClient(create_app(Settings(), app)) as client:
        book = post(client, app, "expand", {"course_ref": course_ref.model_dump()}).json()[
            "book_id"
        ]
        settle(app)
        assert row_of(client, app)["state"] == "failed"
        seen.clear()
        monkeypatch.undo()
        seen = calls(app, monkeypatch)
        assert post(client, app, "retry", {"book_id": book}).status_code == 202
        settle(app)
        assert [r.context["kind"] for r in seen] == ["outline"]
        assert row_of(client, app)["state"] == "outlining"


def test_pictures_route_redraws_and_new_routes_check_input(application, monkeypatch):
    app = application
    adapter = app.models.adapters["simulated"]
    original, images = adapter.invoke, []

    async def invoke(request, binding):
        if request.media == "image":
            images.append(request)
            if len(images) == 1:
                return AdapterReply(data=None, failure="unavailable")
        return await original(request, binding)

    monkeypatch.setattr(adapter, "invoke", invoke)
    book = confirmed(app, taken(app))
    write(app, book)
    with TestClient(create_app(Settings(), app)) as client:
        assert row_of(client, app)["missing_pictures"] == 1
        reply = post(client, app, "pictures", {"book_id": book})
        assert reply.status_code == 202 and reply.json()["job"] == f"reading:{book}"
        settle(app)
        assert row_of(client, app)["missing_pictures"] == 0
        assert post(client, app, "pictures", {"book_id": book}).status_code == 409
        assert post(client, app, "confirm", {"book_id": book}).status_code == 409
        for feedback in ("", "x" * 2001):
            body = {"book_id": book, "feedback": feedback}
            assert post(client, app, "outline", body).status_code == 422
        for route in ("outline", "confirm", "discard", "pictures"):
            body = (
                {"book_id": "../x", "feedback": "x"} if route == "outline" else {"book_id": "../x"}
            )
            assert post(client, app, route, body).status_code == 422
            assert client.post(f"/api/books/{route}", json=body).status_code == 401
