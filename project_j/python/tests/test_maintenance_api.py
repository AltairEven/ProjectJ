import asyncio
import signal
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, confirmed, prepare_course, settle

from project_j.application.configuration import Settings
from project_j.application.documents import document, identity
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.corrections import correct, release
from project_j.interface import api
from project_j.interface.api import create_app
from project_j.learning.maintenance import decide, review
from project_j.platform.storage import (
    Conflict,
    Publication,
    SaveKey,
    StorageBlocked,
    hold_run,
    serving,
)


def test_E2E08_12_method_candidate_and_dimension_catalog_do_not_change_effective_evidence(
    application,
):
    app = application
    asyncio.run(app.prepare("prepare", LEARNER))
    jane = app.store.current(identity("shared", "jane"), LEARNER)
    operation = app.request(
        "candidate", LEARNER, "jane-candidate", {"jane_ref": jane.ref.model_dump()}
    )
    candidate = document(
        "jane",
        jane.object_id,
        operation,
        None,
        jane.body | {"status": "candidate", "change": "模拟候选换表征"},
        revision=2,
        sources=(jane.ref,),
    )
    app.save_step(operation, LEARNER, "candidate", [candidate])
    assert app.store.current(jane.object_id, LEARNER).ref == jane.ref
    before = app.current(LEARNER, "state")
    catalog = document(
        "capability",
        identity("shared", "science.catalog"),
        "catalog-operation",
        None,
        {
            "status": "checked",
            "dimension_id": "science.observation.report",
            "aliases": ["科学报告"],
            "definition": "只报告实际条件，不凭语言表达推断科学理解。",
        },
    )
    app.store.save(
        SaveKey(operation_id="catalog-operation", step_id="catalog", source_revision=1),
        LEARNER,
        [catalog],
    )
    assert app.current(LEARNER, "state").ref == before.ref


def test_E2E10_real_and_adult_sources_cannot_enter_simulation_or_family(application):
    state = application.current(LEARNER, "state")
    for values in (
        {"source_type": "adult"},
        {"source_type": "technical"},
        {"realm": "family", "source_type": "child"},
    ):
        with pytest.raises(ValueError):
            Document.model_validate(state.model_dump() | values)


def test_E2E15_16_reviews_reuse_evidence_and_stale_proposals_do_not_apply(application):
    app = application
    before = app.current(LEARNER, "state")
    for level in ("week", "stage", "annual", "long_term"):
        result = asyncio.run(review(app, f"review:{level}", LEARNER, level))
        assert result["plan_effect"] == "reused"
    assert app.current(LEARNER, "state").ref == before.ref
    assert not app.store.list_current(LEARNER, "fact")
    proposal = asyncio.run(
        review(
            app,
            "proposal",
            LEARNER,
            "week",
            {"verification": "在相近新材料中观察，不预填保持或迁移。"},
        )
    )
    old_plan = app.current(LEARNER, "plan:week")
    decision_ref = RevisionRef.model_validate(proposal["decision_ref"])
    result = decide(app, "accept", LEARNER, decision_ref, "accept")
    assert result["effect"] == "plan_effective"
    assert app.current(LEARNER, "plan:week").revision == old_plan.revision + 1
    second = asyncio.run(
        review(app, "proposal2", LEARNER, "week", {"verification": "另一项模拟复查"})
    )
    old = app.current(LEARNER, "plan:week")
    update = document(
        "plan",
        old.object_id,
        "newer-plan",
        LEARNER,
        old.body,
        revision=old.revision + 1,
        refs=old.dependency_refs,
    )
    app.store.save(
        SaveKey(operation_id="newer-plan", step_id="plan", source_revision=1),
        LEARNER,
        [update],
        (Publication(ref=update.ref, expected_revision=old.revision, reason="明确模拟较新计划"),),
    )
    with pytest.raises(Conflict, match="baseline changed"):
        decide(
            app,
            "accept-stale",
            LEARNER,
            RevisionRef.model_validate(second["decision_ref"]),
            "accept",
        )
    assert app.current(LEARNER, "plan:week").ref == update.ref


def test_a_proposed_goal_outside_the_capability_catalog_is_rejected(application):
    patch = {"main_goal": {"dimension_id": "no.such.dimension", "description": "换个说法的新目标"}}
    with pytest.raises(ValueError, match="established capability identity"):
        asyncio.run(review(application, "unknown-goal", LEARNER, "week", patch))
    assert not application.store.list_current(LEARNER, "decision")


def test_E2E08_content_restriction_owner_and_versioned_release(application):
    app = application
    prepared = asyncio.run(app.prepare("prepare", LEARNER))
    target = RevisionRef.model_validate(prepared["course_ref"])
    result = correct(
        app, "correction", LEARNER, target, "模拟内容缺口，需原主责复核。", "curriculum"
    )
    restriction = RevisionRef.model_validate(result["restriction_refs"][0])
    with pytest.raises(ValueError, match="owner"):
        release(app, "wrong-owner", LEARNER, restriction, "platform", "无权解除")
    released = release(
        app,
        "recheck",
        LEARNER,
        restriction,
        "curriculum",
        "模拟主责对固定版本完成复核，具体范围保留。",
    )
    assert app.store.read(restriction, LEARNER).body["restriction_state"] == "active"
    assert (
        app.store.read(RevisionRef.model_validate(released["restriction_ref"]), LEARNER).body[
            "restriction_state"
        ]
        == "resolved"
    )


def test_E2E17_simulation_report_never_grants_real_interface_or_child_qualification(application):
    dashboard = application.dashboard(LEARNER)
    assert dashboard["qualification"] == "not_granted"
    assert dashboard["actual_api"] == "unverified"
    assert dashboard["actual_voice"] == "unverified"
    with pytest.raises(ValueError):
        Settings(realm="family")


def test_serve_uses_explicit_simulation_root_and_keeps_default(monkeypatch, tmp_path):
    started, handlers = [], {}

    def run(server):
        server.started = True
        started.append(server.config.app)

    monkeypatch.setattr(api.uvicorn.Server, "run", run)
    monkeypatch.setattr(api.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    root = tmp_path / "simulation"
    monkeypatch.setattr(
        sys, "argv", ["project-j-serve", "--run-id", "r1", "--simulation-root", str(root)]
    )
    api.main()
    assert started and (root / "r1" / "project_j.sqlite3").is_file()
    assert handlers == {signal.SIGHUP: api.hang_up}  # Closing the terminal window stops cleanly.
    assert serving(root / "r1")  # Export and reset of this run wait until it stops.
    with pytest.raises(StorageBlocked):
        hold_run(root / "r1")  # Nor can a second service open the same run.
    default = Settings()
    assert (
        default.project_root / default.simulation_root
        == default.project_root / "project_j/private/simulation"
    )


def test_closing_the_terminal_window_stops_like_ctrl_c_and_logs_it(monkeypatch, caplog):
    raised = []
    monkeypatch.setattr(api.signal, "raise_signal", raised.append)
    with caplog.at_level("INFO", logger=api.log.name):
        api.hang_up(signal.SIGHUP, None)
    assert raised == [signal.SIGTERM]  # Uvicorn's graceful stop; a repeated one does not force.
    assert "stop signal=SIGHUP" in caplog.text


def test_the_page_opens_once_the_service_listens_and_the_token_stays_out_of_logs(
    monkeypatch, caplog
):
    opened, link = [], "http://127.0.0.1:8765/#token=secret-token"
    monkeypatch.setattr(api.webbrowser, "open", opened.append)
    with caplog.at_level("INFO", logger=api.log.name):
        api.open_when_started(SimpleNamespace(started=False, should_exit=True), link)
        assert not opened  # Stopped before listening: nothing to open.
        api.open_when_started(SimpleNamespace(started=True, should_exit=False), link)
    assert opened == [link]
    assert "browser opened" in caplog.text and "secret-token" not in caplog.text


def test_API_needs_the_token_and_class_view_hides_teacher_material(application):
    app = application
    fastapi = create_app(Settings(), app)
    with TestClient(fastapi) as client:
        parent = {"Authorization": f"Bearer {app.token}"}
        assert client.get("/api/dashboard").status_code == 401
        prepared = prepare_course(app, "prepare-api")
        confirmed(app, prepared)
        response = app.start(
            "start-api",
            LEARNER,
            RevisionRef.model_validate(prepared["course_ref"]),
            materials_ready=True,
        )
        assert response["decision"] == "allow_simulation"
        student = parent
        snapshot = client.get("/api/class/snapshot", headers=student).json()
        assert not snapshot["materials"]
        assert (snapshot["task_index"], snapshot["task_count"]) == (-1, len(app.core.course.tasks))
        assert "acceptable_simulated" not in str(snapshot) and "supports" not in str(snapshot)
        parent_during_class = client.get("/api/dashboard", headers=parent).json()
        assert parent_during_class["objects"] == [] and parent_during_class["active"]
        assert parent_during_class["verification"] == app.verification
        stopped = client.post(
            "/api/class/control",
            headers=student,
            json={"event_id": "stop-api", "command": "stop"},
        )
        assert stopped.status_code == 200 and stopped.json()["mode"] == "ended"
        dashboard = client.get("/api/dashboard", headers=parent).json()
        assert any(doc["kind"] == "fact" for doc in dashboard["objects"])
        assert (
            client.post(
                "/api/review",
                headers=parent,
                json={"request_id": "family-attempt", "level": "week", "realm": "family"},
            ).status_code
            == 422
        )


def test_API_websocket_reconnect_reads_the_same_memory_core(application):
    app = application
    with TestClient(create_app(Settings(), app)) as client:
        prepared = prepare_course(app, "prep-ws")
        confirmed(app, prepared)
        started = app.start(
            "start-ws",
            LEARNER,
            RevisionRef.model_validate(prepared["course_ref"]),
            materials_ready=True,
        )
        protocol = f"project-j.{app.token}"
        with client.websocket_connect("/api/class/events", subprotocols=[protocol]) as socket:
            assert (
                socket.receive_json()["snapshot"]["activity_id"]
                == started["snapshot"]["activity_id"]
            )
        with client.websocket_connect("/api/class/events", subprotocols=[protocol]) as socket:
            assert (
                socket.receive_json()["snapshot"]["activity_id"]
                == started["snapshot"]["activity_id"]
            )
            socket.send_json(
                {
                    "id": "stop",
                    "kind": "control",
                    "payload": {"event_id": "stop-ws", "command": "stop"},
                }
            )
            assert socket.receive_json()["snapshot"]["mode"] == "ended"


def test_E2E12_scoped_aliases_and_split_do_not_duplicate_old_learning_evidence(application):
    from project_j.learning.catalog import Capability, define, resolve

    app = application
    original = resolve(app, LEARNER, "english.reading", "Story order")
    assert original.body["dimension_id"] == "english.reading.facts"
    state = app.current(LEARNER, "state")
    math = Capability(
        dimension_id="math.order.size",
        subject="math",
        name="大小排序",
        definition="依据大小关系排序，不能用故事排序代替。",
        aliases=("排序",),
    )
    define(app, "math-catalog", LEARNER, math)
    assert resolve(app, LEARNER, "math", "排序").ref != original.ref
    with pytest.raises(Conflict):
        define(
            app,
            "wrong-alias",
            LEARNER,
            Capability(
                dimension_id="english.reading.other",
                subject="english.reading",
                name="Story order",
                definition="同名不能创造新的能力身份。",
            ),
        )
    detail = Capability(
        dimension_id="english.reading.sequence",
        subject="english.reading",
        name="文本先后关系",
        definition="限定文本先后关系的细分。",
        split_from=original.ref,
    )
    define(app, "split-catalog", LEARNER, detail)
    assert app.current(LEARNER, "state").ref == state.ref
    assert all(row["dimension_id"] != detail.dimension_id for row in state.body["dimensions"])
    assert not app.store.list_current(LEARNER, "fact")


def test_old_student_socket_and_parent_control_cannot_access_the_next_activity(application):
    from starlette.websockets import WebSocketDisconnect

    app = application
    with TestClient(create_app(Settings(), app)) as client:
        parent = {"Authorization": f"Bearer {app.token}"}
        package = prepare_course(app, "scope-prepare")
        confirmed(app, package)
        first = app.start(
            "scope-first",
            LEARNER,
            RevisionRef.model_validate(package["course_ref"]),
            materials_ready=True,
        )
        student = {"Authorization": f"Bearer {app.token}"}
        with client.websocket_connect(
            "/api/class/events", subprotocols=[f"project-j.{app.token}"]
        ) as socket:
            socket.receive_json()
            assert (
                client.get("/api/dashboard?learner_id=fictional-L2", headers=parent).status_code
                == 403
            )
            client.post(
                "/api/class/control",
                headers=student,
                json={"event_id": "scope-stop", "command": "stop"},
            )
            settle(app)  # the class end seals and evaluates in the service thread
            second = app.start(
                "scope-second",
                LEARNER,
                RevisionRef.model_validate(package["course_ref"]),
                materials_ready=True,
            )
            assert second["decision"] == "allow_simulation"
            old = client.post(
                "/api/class/control",
                headers=parent,
                json={
                    "event_id": "old-parent-stop",
                    "activity_id": first["snapshot"]["activity_id"],
                    "command": "stop",
                },
            )
            assert old.status_code == 409
            socket.send_json(
                {"kind": "control", "payload": {"event_id": "old-ws", "command": "stop"}}
            )
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1008
            assert app.core.activity_id == second["snapshot"]["activity_id"]
            assert app.core.mode != "ended"


def test_one_token_covers_management_and_the_class(application):
    from starlette.websockets import WebSocketDisconnect

    app = application
    with TestClient(create_app(Settings(), app)) as client:
        auth = {"Authorization": f"Bearer {app.token}"}
        assert client.get("/api/dashboard", headers=auth).status_code == 200
        assert client.get("/api/dashboard").status_code == 401
        assert (
            client.get("/api/dashboard", headers={"Authorization": "Bearer x"}).status_code == 403
        )
        assert client.get("/api/class/snapshot", headers=auth).status_code == 403  # no class yet
        package = prepare_course(app, "one-prepare")
        confirmed(app, package)
        started = app.start(
            "one-start",
            LEARNER,
            RevisionRef.model_validate(package["course_ref"]),
            materials_ready=True,
        )
        assert "student_token" not in started
        assert client.get("/api/class/snapshot", headers=auth).status_code == 200
        with client.websocket_connect(
            "/api/class/events", subprotocols=[f"project-j.{app.token}"]
        ) as socket:
            assert socket.receive_json()["snapshot"]["activity_id"] == app.core.activity_id
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/class/events", subprotocols=["project-j.x"]):
                pass


def test_focus_route_refuses_more_than_three_skills(application):
    from test_progress import waiting_demand

    demand = waiting_demand(application)
    with TestClient(create_app(Settings(), application)) as client:
        auth = {"Authorization": f"Bearer {application.token}"}
        body = {"request_id": "f1", "learner_id": LEARNER, "demand_ref": demand.ref.model_dump()}
        body |= {"topic": "塞尔达", "skills": [], "note": None}
        many = body | {"skills": ["verbs", "adjectives", "adverbs", "articles"]}
        assert client.post("/api/focus", headers=auth, json=many).status_code == 400
        for n, topic in enumerate(("   ", "x" * 101)):
            blank = body | {"request_id": f"t{n}", "topic": topic}
            assert client.post("/api/focus", headers=auth, json=blank).status_code == 400
        assert client.get("/api/dashboard", headers=auth).json()["next"]["kind"] == "focus"
        none = body | {"request_id": "f2"}
        assert client.post("/api/focus", headers=auth, json=none).status_code == 200
