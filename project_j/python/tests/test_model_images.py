import asyncio
import base64
import contextlib
import json
import logging
import sqlite3
import time

import pytest
from openai_codex import ImageInput, TextInput
from openai_codex.generated.v2_all import ItemCompletedNotification
from openai_codex.models import Notification
from pydantic import BaseModel
from test_models_codex import (  # private_tmp keeps adapter work directories here.
    FakeCodex,
    adapter,
    command,
    completed,
    message,
    private_tmp,  # noqa: F401
)

from project_j.platform.models.codex_sdk import BASE_INSTRUCTIONS, IMAGE_INSTRUCTIONS
from project_j.platform.models.contracts import (
    DEFAULT_DEADLINES,
    AdapterReply,
    Binding,
    CapabilityReport,
    ImageNote,
    ModelRequest,
    ProbeItem,
    solid_png,
)
from project_j.platform.models.service import ModelService
from project_j.platform.models.simulated import SimulatedAdapter
from project_j.platform.visuals import image_type

BINDING = Binding(adapter="codex", model="gpt-6.1-sol", effort="xhigh")
DRAW = ModelRequest(
    "preparation",
    "draw",
    {"content": "a paper bridge"},
    ImageNote,
    media="image",
    deadline_seconds=10,
)
NOTE = json.dumps({"description": "a paper bridge between two books"})


def drawn(result="", failure=None, saved_path=None):
    item = {"type": "imageGeneration", "id": "i", "result": result, "status": "completed"}
    if failure:
        item["failure"] = {"type": "usageLimitExceeded", "limitId": "images"}
    if saved_path:
        item["savedPath"] = str(saved_path)
    return Notification(
        "item/completed",
        ItemCompletedNotification.model_validate(
            {"completedAtMs": 1, "threadId": "th", "turnId": "tu", "item": item}
        ),
    )


PNG = base64.b64encode(solid_png()).decode()


def mcp(tool, server="codex"):
    item = {
        "type": "mcpToolCall",
        "id": tool,
        "server": server,
        "tool": tool,
        "status": "completed",
        "arguments": {},
    }
    return Notification(
        "item/completed",
        ItemCompletedNotification.model_validate(
            {"completedAtMs": 1, "threadId": "th", "turnId": "tu", "item": item}
        ),
    )


def test_simulated_image_request_returns_one_png():
    result = asyncio.run(ModelService.simulated().run(DRAW))
    assert result.ok and len(result.media) == 1 and image_type(result.media[0]) == "png"
    assert result.output.description


def test_codex_takes_the_generated_image(tmp_path):
    # The runtime also saves every picture under its home; no copy of it stays there.
    saved = tmp_path / "generated_images" / "thread" / "exec-1.png"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(solid_png())
    fake = FakeCodex(events=[drawn(PNG, saved_path=saved), message(NOTE), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING))
    assert reply.failure is None and reply.media == (solid_png(),)
    assert not saved.parent.exists()
    assert reply.actual["tools"].status == "verified"
    assert reply.actual["tools"].requested == "imageGeneration"
    assert fake.starts[0]["base_instructions"] == IMAGE_INSTRUCTIONS


def test_codex_reads_and_removes_a_saved_image(tmp_path):
    saved = tmp_path / "image.png"
    saved.write_bytes(solid_png())
    fake = FakeCodex(events=[drawn(saved_path=saved), message(NOTE), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING))
    assert reply.media == (solid_png(),) and not saved.exists()


def test_codex_image_limit_is_quota(tmp_path):
    fake = FakeCodex(events=[drawn(failure=True), message(NOTE), completed()])
    assert asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING)).failure == "quota"


def test_codex_without_an_image_is_incomplete(tmp_path):
    fake = FakeCodex(events=[message(NOTE), completed()])
    assert asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING)).failure == "incomplete"


# Seen in the runtime's own log (2026-11-21): the image tool's error never reaches the client,
# only an ERROR row whose body names the turn; the request ID in it is made up here.
IP_ERROR = (
    "dispatch{thread.id=th turn.id=tu}: error=image generation failed: http 401 Unauthorized: "
    'Some("{\\n  \\"error\\": {\\n    \\"message\\": \\"Your IP is not authorized to make '
    'this request.\\",\\n    \\"type\\": \\"invalid_request_error\\"\\n  }\\n}")'
)
SAFETY_ERROR = (
    "dispatch{thread.id=th turn.id=tu}: error=image generation failed: http 400 Bad Request: "
    'Some("{\\n  \\"error\\": {\\n    \\"message\\": \\"Your request was rejected by the '
    "safety system. If you believe this is an error, contact us and include the request ID "
    '00000000-0000-0000-0000-000000000000.\\",\\n    '
    '\\"code\\": \\"moderation_blocked\\"\\n  }\\n}")'
)


def runtime_log(home, body):
    """The runtime's log database with one image-tool error row, its columns as seen."""
    home.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(home / "logs_2.sqlite")) as db:
        db.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, "
            "ts_nanos INTEGER NOT NULL, level TEXT NOT NULL, target TEXT NOT NULL, "
            "feedback_log_body TEXT, thread_id TEXT)"
        )
        db.execute(
            "INSERT INTO logs (ts, ts_nanos, level, target, feedback_log_body) "
            "VALUES (?, 0, 'ERROR', 'codex_core::tools::router', ?)",
            (int(time.time()), body),
        )
        db.commit()


@pytest.mark.parametrize(
    ("body", "kind", "lookup"),
    [
        (IP_ERROR, "network_blocked", "network_blocked"),
        (SAFETY_ERROR, "moderated", "moderated"),
        (IP_ERROR.replace("turn.id=tu", "turn.id=other"), "incomplete", "none"),
        (None, "incomplete", "unreadable"),  # No log database: nothing to tell.
    ],
    ids=["network", "safety", "another turn", "no log"],
)
def test_codex_names_why_the_image_tool_drew_nothing(tmp_path, caplog, body, kind, lookup):
    caplog.set_level(logging.INFO, logger="project_j")
    if body:
        runtime_log(tmp_path / "codex-home", body)
    fake = FakeCodex(events=[message(NOTE), completed()])
    assert asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING)).failure == kind
    assert f"codex image error lookup={lookup} ms=" in caplog.text
    assert "authorized" not in caplog.text and "safety" not in caplog.text


def test_codex_image_request_refuses_other_tools(tmp_path):
    fake = FakeCodex(events=[command(), drawn(PNG), message(NOTE), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING))
    assert reply.actual["tools"].status == "mismatch"


def test_codex_picture_may_list_mcp_resources(tmp_path):
    # Seen on the real runtime (2026-10-20): the picture's code may first list the runtime's
    # own MCP resources. Read-only and empty in this isolated home, so it does not fail.
    listing = [mcp("list_mcp_resources"), mcp("list_mcp_resource_templates")]
    fake = FakeCodex(events=[*listing, drawn(PNG), message(NOTE), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING))
    assert reply.failure is None and reply.actual["tools"].status == "verified"
    assert reply.actual["tools"].actual == (
        "imageGeneration,mcpToolCall:codex/list_mcp_resource_templates,"
        "mcpToolCall:codex/list_mcp_resources"
    )


@pytest.mark.parametrize("call", [mcp("read_mcp_resource"), mcp("list_mcp_resources", "other")])
def test_codex_picture_refuses_other_mcp_calls(tmp_path, call):
    fake = FakeCodex(events=[call, drawn(PNG), message(NOTE), completed()])
    reply = asyncio.run(adapter(fake, tmp_path).invoke(DRAW, BINDING))
    assert reply.actual["tools"].status == "mismatch"


def test_codex_text_request_refuses_mcp_listing(tmp_path):
    fake = FakeCodex(events=[mcp("list_mcp_resources"), message(NOTE), completed()])
    text = ModelRequest("preparation", "write", {"content": "x"}, ImageNote, deadline_seconds=10)
    reply = asyncio.run(adapter(fake, tmp_path).invoke(text, BINDING))
    assert reply.actual["tools"].status == "mismatch"


def test_codex_text_request_still_refuses_drawing(tmp_path):
    fake = FakeCodex(events=[drawn(PNG), message(NOTE), completed()])
    text = ModelRequest("preparation", "write", {"content": "x"}, ImageNote, deadline_seconds=10)
    reply = asyncio.run(adapter(fake, tmp_path).invoke(text, BINDING))
    assert reply.actual["tools"].status == "mismatch" and reply.media == ()
    assert fake.starts[0]["base_instructions"] == BASE_INSTRUCTIONS


class Drawer:
    adapter_id, version, simulated = "drawer", "1", False
    efforts, tiers = ("xhigh",), ("default",)

    def __init__(self, media):
        self.media = media

    async def invoke(self, request, binding):
        return AdapterReply(data={"description": "d"}, media=self.media)


def drawer_service(media):
    service = ModelService.simulated()
    binding = Binding(
        adapter="drawer", model=service.bindings["preparation"].model, effort="medium"
    )
    service.adapters["drawer"] = Drawer(media)
    service.bindings["preparation"] = binding
    service.reports.append(
        service.reports[0].model_copy(update={"adapter": "drawer", "model": binding.model})
    )
    return service


def test_media_must_be_exactly_one_image(caplog):
    for media in ((b"not an image",), (), (solid_png(), solid_png())):
        result = asyncio.run(drawer_service(media).run(DRAW))
        assert result.failure == "invalid_output" and result.media == ()
    with caplog.at_level(logging.INFO):
        result = asyncio.run(drawer_service((solid_png(),)).run(DRAW))
    assert result.ok and f"media=1 media_bytes={len(solid_png())}" in caplog.text


class Verdicts(BaseModel):
    pictures: list[dict]


LOOK = ModelRequest(
    "preparation",
    "look",
    {"kind": "check"},
    Verdicts,
    deadline_key="preparation_image_check",
    images=(solid_png(), solid_png(), solid_png()),
)


def test_a_request_draws_or_looks_not_both():
    with pytest.raises(ValueError, match="not both"):
        ModelRequest(
            "preparation",
            "x",
            {},
            ImageNote,
            deadline_key="preparation_image",
            media="image",
            images=(solid_png(),),
        )


def test_picture_check_has_its_own_deadline():
    assert DEFAULT_DEADLINES["preparation_image_check"] == 300


def test_codex_sends_pictures_as_image_inputs(tmp_path):
    fake = FakeCodex(events=[message(NOTE), completed()])
    asyncio.run(adapter(fake, tmp_path).invoke(LOOK, BINDING))
    sent = fake.turns[0][0]
    assert isinstance(sent[0], TextInput) and json.loads(sent[0].text) == {"kind": "check"}
    assert [type(item) for item in sent[1:]] == [ImageInput] * 3
    assert sent[1].url == "data:image/png;base64," + PNG
    assert fake.starts[0]["base_instructions"] == BASE_INSTRUCTIONS  # Looking draws nothing.


def test_sees_images_follows_the_probe():
    service = ModelService.simulated()
    assert service.sees_images("preparation")
    report = service.reports[0]
    items = report.items | {"image_input": ProbeItem(status="unsupported")}
    service.reports = [CapabilityReport(**report.model_dump() | {"items": items})]
    assert not service.sees_images("preparation")
    del service.bindings["preparation"]
    assert not service.sees_images("preparation")


def test_simulated_check_rejects_preset_pictures():
    adapter_ = SimulatedAdapter()
    adapter_.reject_pictures = [{2}]
    first = asyncio.run(adapter_.invoke(LOOK, BINDING)).data["pictures"]
    assert [item["ok"] for item in first] == [True, False, True]
    assert first[1]["reason"] == "wrong" and first[0]["reason"] is None
    again = asyncio.run(adapter_.invoke(LOOK, BINDING)).data["pictures"]
    assert all(item["ok"] for item in again)


def test_model_log_counts_pictures_sent(caplog):
    with caplog.at_level(logging.INFO):
        result = asyncio.run(ModelService.simulated().run(LOOK))
    assert result.ok and "images=3" in caplog.text
    assert PNG not in caplog.text
