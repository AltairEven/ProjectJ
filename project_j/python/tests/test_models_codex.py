import asyncio
import dataclasses
import json
import logging
import tempfile
import time
from pathlib import Path

import pytest
from openai_codex import ApprovalMode, ImageInput, Sandbox, TransportClosedError
from openai_codex.generated.v2_all import (
    GetAccountResponse,
    ItemCompletedNotification,
    ModelListResponse,
    TurnCompletedNotification,
)
from openai_codex.models import Notification

from project_j.classroom.judgment import ClassroomJudgment
from project_j.platform.models.codex_sdk import BASE_INSTRUCTIONS, CodexAdapter, codex_config
from project_j.platform.models.contracts import Binding, ImageNote, ModelRequest

BINDING = Binding(adapter="codex", model="gpt-6.1-sol", effort="low")
REQUEST = ModelRequest(
    "classroom_judgment",
    "judge",
    {"evidence": "e", "answer": "a bridge"},
    ClassroomJudgment,
    deadline_seconds=10,
)
GOOD = json.dumps({"judgment": "correct", "evidence": "e", "text": "对"})
PROBE_GOOD = json.dumps({"verdict": "correct", "evidence": "a bridge", "response": "Yes."})


@pytest.fixture(autouse=True)
def private_tmp(tmp_path, monkeypatch):
    """Adapter work directories land in this test's directory, not the system one."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def message(text):
    return Notification(
        "item/completed",
        ItemCompletedNotification.model_validate(
            {
                "completedAtMs": 1,
                "threadId": "th",
                "turnId": "tu",
                "item": {"type": "agentMessage", "id": "m", "text": text, "phase": "final_answer"},
            }
        ),
    )


def command():
    item = {
        "type": "commandExecution",
        "id": "c",
        "command": "ls ~",
        "cwd": "/tmp",
        "status": "completed",
        "commandActions": [],
    }
    return Notification(
        "item/completed",
        ItemCompletedNotification.model_validate(
            {"completedAtMs": 1, "threadId": "th", "turnId": "tu", "item": item}
        ),
    )


def completed(status="completed", error_info=None):
    error = {"message": "failed", "codexErrorInfo": error_info} if error_info else None
    turn = {"id": "tu", "items": [], "status": status, "error": error}
    return Notification(
        "turn/completed",
        TurnCompletedNotification.model_validate({"threadId": "th", "turn": turn}),
    )


class FakeHandle:
    def __init__(self, events, hang=False):
        self.id, self.events, self.hang, self.interrupted = "tu", events, hang, False

    async def stream(self):
        for event in self.events:
            yield event
        if self.hang:
            await asyncio.sleep(10)

    async def interrupt(self):
        self.interrupted = True


class FakeThread:
    def __init__(self, codex):
        self.codex = codex

    async def turn(self, input, **options):
        self.codex.turns.append((input, options))
        return self.codex.next_handle(input)


class FakeCodex:
    """Stands in for openai_codex.AsyncCodex; never launches the binary."""

    def __init__(self, events=None, account=True, models=("gpt-6.1-sol",), hang=False, fast=True):
        self.events = events if events is not None else [message(GOOD), completed()]
        self.account_present, self.model_ids, self.hang = account, models, hang
        self.fast = fast
        self.starts, self.turns, self.handles, self.closed = [], [], [], False

    def next_handle(self, input):
        events = self.events
        if isinstance(input, list):  # image probe
            events = [message(json.dumps({"color": "Red"})), completed()]
        elif "Mia" in str(input):  # text probe
            events = [message(PROBE_GOOD), completed()]
        handle = FakeHandle(events, hang=self.hang)
        self.handles.append(handle)
        return handle

    async def thread_start(self, **options):
        self.starts.append(options)
        return FakeThread(self)

    async def account(self):
        account = {"type": "chatgpt", "email": "x@example.com", "planType": "plus"}
        return GetAccountResponse.model_validate(
            {"account": account if self.account_present else None, "requiresOpenaiAuth": True}
        )

    async def models(self):
        data = [
            {
                "id": model_id,
                "model": model_id,
                "description": "",
                "displayName": model_id,
                "hidden": False,
                "isDefault": True,
                "defaultReasoningEffort": "medium",
                "supportedReasoningEfforts": [
                    {"description": "", "reasoningEffort": effort} for effort in ("low", "medium")
                ],
                "serviceTiers": [{"description": "2x speed", "id": "priority", "name": "Fast"}]
                if self.fast
                else [],
            }
            for model_id in self.model_ids
        ]
        return ModelListResponse.model_validate({"data": data, "nextCursor": None})

    async def close(self):
        self.closed = True


def adapter(fake, tmp_path):
    return CodexAdapter(tmp_path / "codex-home", factory=lambda workdir, image: fake)


def test_each_call_is_an_isolated_read_only_ephemeral_thread(tmp_path):
    fake = FakeCodex()
    reply = asyncio.run(adapter(fake, tmp_path).invoke(REQUEST, BINDING))
    start = fake.starts[0]
    assert start["model"] == "gpt-6.1-sol" and start["ephemeral"] is True
    assert start["sandbox"] is Sandbox.read_only and start["approval_mode"] is ApprovalMode.deny_all
    assert start["developer_instructions"] == "judge"
    assert start["base_instructions"] == BASE_INSTRUCTIONS  # replaces Codex's agent prompt
    workdir = Path(start["cwd"])
    assert workdir.is_dir() and list(workdir.iterdir()) == []  # no project files or AGENTS.md
    text, options = fake.turns[0]
    assert json.loads(text) == REQUEST.context and options["effort"] == "low"
    schema = options["output_schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(
        ClassroomJudgment.model_fields
    )
    assert reply.failure is None and reply.data["judgment"] == "correct"
    assert {name: c.status for name, c in reply.actual.items()} == {
        "model": "unverified",
        "effort": "unverified",
        "tools": "verified",
    }


def test_fast_tier_requests_the_priority_service_tier(tmp_path):
    fake = FakeCodex()
    codex = adapter(fake, tmp_path)
    asyncio.run(codex.invoke(REQUEST, BINDING))
    fast = BINDING.model_copy(update={"tier": "fast"})
    reply = asyncio.run(codex.invoke(REQUEST, fast))
    assert fake.starts[0]["service_tier"] is None and fake.starts[1]["service_tier"] == "priority"
    assert reply.actual["service_tier"].requested == "fast"
    assert reply.actual["service_tier"].status == "unverified"


def test_a_tool_call_is_an_explicit_mismatch(tmp_path):
    # ModelService turns any mismatch into config_mismatch (test_models_service).
    fake = FakeCodex(events=[command(), message(GOOD), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(REQUEST, BINDING))
    tools = reply.actual["tools"]
    assert tools.status == "mismatch" and tools.actual == "commandExecution"


def test_runtime_uses_its_own_codex_home(tmp_path):
    config = codex_config(tmp_path / "codex-home", tmp_path / "work")
    assert config.env == {"CODEX_HOME": str(tmp_path / "codex-home")}
    assert config.cwd == str(tmp_path / "work")
    # Nothing carries over between calls and no outside text or tools enter a request.
    assert set(config.config_overrides) == {
        "memories.generate_memories=false",
        "memories.use_memories=false",
        'history.persistence="none"',
        "project_doc_max_bytes=0",
        'web_search="disabled"',
        "analytics.enabled=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        # Measured on the real runtime: these cut injected context and stop it growing per call.
        "include_environment_context=false",
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "features.apps=false",
        "features.plugins=false",
        "features.skills=false",
        # Text requests run without code mode: a tool called from code would not show as a
        # turn item, so the tool check could not see it.
        "features.code_mode_host=false",
    }
    # Pictures come only through code mode; that runtime is used for nothing else.
    drawing = codex_config(tmp_path / "codex-home", tmp_path / "work", image=True)
    assert set(drawing.config_overrides) == set(config.config_overrides) - {
        "features.code_mode_host=false"
    }
    assert CodexAdapter.version == "3"  # behavior changed: old init records must be re-probed
    # The runtime runs in a temporary directory, so a relative home would point elsewhere.
    assert CodexAdapter(Path("project_j/private/models/codex-home")).home.is_absolute()


@pytest.mark.parametrize(
    "events,kind",
    [
        ([completed("failed", "unauthorized")], "auth"),
        ([completed("failed", "usageLimitExceeded")], "quota"),
        ([completed("failed", "rateLimitExceeded")], "quota"),
        ([completed("failed", "cyberPolicy")], "refused"),
        ([completed("failed", "serverOverloaded")], "unavailable"),
        ([completed("interrupted")], "incomplete"),
        ([completed()], "incomplete"),  # no final message
        ([message("not json"), completed()], "invalid_output"),
    ],
)
def test_turn_outcomes_map_to_failure_kinds(tmp_path, events, kind):
    reply = asyncio.run(adapter(FakeCodex(events), tmp_path).invoke(REQUEST, BINDING))
    assert reply.failure == kind and reply.data is None


def test_runtime_errors_become_unavailable_without_text(tmp_path):
    class Broken(FakeCodex):
        async def thread_start(self, **options):
            raise TransportClosedError("account x@example.com token abc")

    reply = asyncio.run(adapter(Broken(), tmp_path).invoke(REQUEST, BINDING))
    assert reply.failure == "unavailable" and "abc" not in repr(reply)


def test_cancelled_call_interrupts_the_server_turn(tmp_path):
    fake = FakeCodex(events=[], hang=True)
    instance = adapter(fake, tmp_path)

    async def scenario():
        job = asyncio.create_task(instance.invoke(REQUEST, BINDING))
        await asyncio.sleep(0.05)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(scenario())
    assert fake.handles[0].interrupted


def test_probe_reports_each_item(tmp_path):
    fake = FakeCodex(hang=False)
    report = asyncio.run(adapter(fake, tmp_path).probe("gpt-6.1-sol", ("low", "high")))
    statuses = {name: item.status for name, item in report.items.items()}
    assert statuses["auth"] == statuses["model"] == statuses["structured_output"] == "verified"
    assert statuses["effort:low"] == "unverified" and statuses["effort:high"] == "unsupported"
    assert (
        statuses["cancellation"] == "verified"
        and "stop requested" in report.items["cancellation"].detail
    )
    assert statuses["image_input"] == "verified" and statuses["audio"] == "not_probed"
    assert statuses["tools"] == "verified"
    text, image = fake.turns[-1][0]
    assert isinstance(image, ImageInput) and image.url.startswith("data:image/png;base64,")
    assert "x@example.com" not in report.model_dump_json() and not report.simulated


def test_probe_reads_the_fast_tier_from_the_subscription_list(tmp_path):
    listed = asyncio.run(adapter(FakeCodex(), tmp_path).probe("gpt-6.1-sol", ("low",), ("fast",)))
    assert listed.items["tier:fast"].status == "unverified"
    missing = asyncio.run(
        adapter(FakeCodex(fast=False), tmp_path).probe("gpt-6.1-sol", ("low",), ("fast",))
    )
    assert missing.items["tier:fast"].status == "unsupported"
    plain = asyncio.run(adapter(FakeCodex(), tmp_path).probe("gpt-6.1-sol", ("low",)))
    assert "tier:fast" not in plain.items


def test_probe_without_login_stops_at_auth(tmp_path):
    report = asyncio.run(adapter(FakeCodex(account=False), tmp_path).probe("gpt-6.1-sol", ("low",)))
    assert report.items["auth"].status == "failed"
    assert "project-j-init login codex" in report.items["auth"].detail
    assert report.items["structured_output"].status == report.items["tools"].status == "not_probed"


def test_probe_flags_a_model_missing_from_the_subscription(tmp_path):
    fake = FakeCodex(models=("gpt-other",))
    report = asyncio.run(adapter(fake, tmp_path).probe("gpt-6.1-sol", ("low",)))
    assert report.items["model"].status == "failed"


def test_pictures_use_a_separate_runtime(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    text, drawing, launched = FakeCodex(), FakeCodex(), []
    draw = ModelRequest(
        "preparation",
        "draw",
        {"content": "a bridge"},
        ImageNote,
        media="image",
        deadline_seconds=10,
    )

    def factory(workdir, image):
        launched.append(image)
        return drawing if image else text

    instance = CodexAdapter(tmp_path / "codex-home", factory=factory)

    async def scenario():
        await instance.invoke(REQUEST, BINDING)
        await instance.invoke(REQUEST, BINDING)
        await instance.invoke(draw, BINDING)
        await instance.aclose()

    asyncio.run(scenario())
    assert launched == [False, True]  # Each launched once, the picture one only when needed.
    assert len(text.starts) == 2 and len(drawing.starts) == 1
    assert text.closed and drawing.closed
    assert "codex runtime launched kind=text" in caplog.text
    assert "codex runtime launched kind=image" in caplog.text


def test_prewarm_starts_the_text_runtime_before_any_request(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    fake, launched = FakeCodex(), []

    def factory(workdir, image):
        launched.append(image)
        return fake

    instance = CodexAdapter(tmp_path / "codex-home", factory=factory)

    async def scenario():
        await instance.prewarm()
        await instance.invoke(REQUEST, BINDING)
        await instance.aclose()

    asyncio.run(scenario())
    assert launched == [False] and len(fake.starts) == 1  # The request reused it.
    assert "codex runtime warmed result=ok ms=" in caplog.text


def test_prewarm_survives_a_runtime_that_cannot_start(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    def factory(workdir, image):
        raise OSError("no binary")

    asyncio.run(CodexAdapter(tmp_path / "codex-home", factory=factory).prewarm())
    assert "codex runtime warmed result=unavailable ms=" in caplog.text


def test_close_stops_runtime_and_removes_workdir(tmp_path):
    fake = FakeCodex()
    instance = adapter(fake, tmp_path)

    async def scenario():
        await instance.invoke(REQUEST, BINDING)
        workdir = Path(fake.starts[0]["cwd"])
        await instance.aclose()
        return workdir

    workdir = asyncio.run(scenario())
    assert fake.closed and not workdir.exists()


def test_each_call_logs_thread_and_turn_time(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(adapter(FakeCodex(), tmp_path).invoke(REQUEST, BINDING))
    assert "codex runtime launched" in caplog.text
    assert "codex thread_ms=" in caplog.text and "status=completed" in caplog.text
    assert "a bridge" not in caplog.text


def test_the_request_deadline_bounds_the_call_not_a_fixed_minute(tmp_path):
    # Evaluation asks for 180 s; a fixed internal 60 s cut it short (adult check, 2026-10-19).
    short = dataclasses.replace(REQUEST, deadline_seconds=0.2)
    fake = FakeCodex(events=[], hang=True)
    started = time.perf_counter()
    reply = asyncio.run(adapter(fake, tmp_path).invoke(short, BINDING))
    assert reply.failure == "timeout" and time.perf_counter() - started < 2
    assert fake.handles[0].interrupted
