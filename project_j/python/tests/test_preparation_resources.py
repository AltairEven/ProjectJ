import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, start
from test_preparation_model import ref, science_draft, science_lesson

from project_j.application.configuration import Settings
from project_j.interface.api import create_app
from project_j.platform.files import PrivateFiles, file_stem
from project_j.platform.models.contracts import AdapterReply, solid_png
from project_j.platform.storage import Conflict, StorageBlocked
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.recorded import decode_wav
from project_j.preparation.drafting import PreparationUnavailable
from project_j.preparation.folders import draft_of
from project_j.preparation.lessons import confirm_resources, lesson_of
from project_j.preparation.resources import (
    adopt_resource,
    generate_resource,
    resource_doc,
    resource_docs,
    resource_file,
    resources_of,
)

UNSAFE = '<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>'


def prepared(application):
    return ref(asyncio.run(application.prepare("prepare1", LEARNER))["course_ref"])


def preview(application, course_ref):
    return next(
        doc
        for doc in application.store.list_current(LEARNER, "preview")
        if doc.body["course_ref"] == course_ref.model_dump()
    )


def resource(application, course_ref, resource_id):
    return resource_doc(application, LEARNER, course_ref, resource_id).body


def demo_of(application, course_ref):
    """Before confirmation only the course lists it; after, its own record."""
    items = (
        resources_of(application, LEARNER, course_ref)
        or preview(application, course_ref).body["resources"]
    )
    return next(item for item in items if item["resource_id"].startswith("demo:"))


def generate(application, request_id, course_ref, resource_id):
    return asyncio.run(generate_resource(application, request_id, LEARNER, course_ref, resource_id))


def test_resources_wait_for_confirmation(application):
    course_ref = prepared(application)
    demo = demo_of(application, course_ref)
    assert demo["state"] == "planned"
    with pytest.raises(Conflict, match="Confirm the lesson first"):
        generate(application, "g0", course_ref, demo["resource_id"])


def test_confirming_the_content_lists_each_resource_as_its_own_record(application):
    course_ref = prepared(application)
    application.confirm("c1", LEARNER, course_ref)
    docs = resource_docs(application, LEARNER, course_ref)
    spec = application.store.read(course_ref, LEARNER).body["resources"]
    assert [doc.body["resource_id"] for doc in docs] == [item["resource_id"] for item in spec]
    assert all(doc.kind == "resource" for doc in docs)
    assert all(doc.body["course_ref"] == course_ref.model_dump() for doc in docs)
    assert {doc.body["state"] for doc in docs} <= {"awaiting_generation", "not_connected"}


def test_a_resource_change_saves_only_that_resource(application):
    course_ref = prepared(application)
    application.confirm("c1", LEARNER, course_ref)
    before = preview(application, course_ref).revision
    demo = demo_of(application, course_ref)
    generate(application, "g1", course_ref, demo["resource_id"])
    adopt_resource(application, "a1", LEARNER, course_ref, demo["resource_id"], 1)
    assert preview(application, course_ref).revision == before
    doc = resource_doc(application, LEARNER, course_ref, demo["resource_id"])
    draft = draft_of(application.store, LEARNER, course_ref.object_id)
    assert doc.revision == 3  # Listed, generated, adopted.
    assert doc.body["versions"][0]["asset"]["path"] == (
        f"{draft}/audio/{file_stem(demo['resource_id'])}.v1.wav"
    )


def test_generate_regenerate_and_adopt_audio(application, caplog):
    course_ref = prepared(application)
    application.confirm("c1", LEARNER, course_ref)
    demo = demo_of(application, course_ref)
    assert (demo["state"], demo["versions"], demo["adopted_version"]) == (
        "awaiting_generation",
        [],
        None,
    )
    caplog.set_level(logging.INFO, logger="project_j")
    first = generate(application, "g1", course_ref, demo["resource_id"])
    assert (first["version"], first["state"]) == (1, "generated")
    calls = len(application.models.calls)
    assert generate(application, "g1", course_ref, demo["resource_id"]) == first
    generate(application, "g2", course_ref, demo["resource_id"])
    entry = resource(application, course_ref, demo["resource_id"])
    assert [item["version"] for item in entry["versions"]] == [1, 2]
    assert entry["state"] == "generated" and entry["adopted_version"] is None
    assert entry["versions"][0]["source"]["voice"] == application.synthesizer.voice
    data, media_type = resource_file(application, LEARNER, course_ref, demo["resource_id"], 1)
    rate, pcm = decode_wav(data)
    assert media_type == "audio/wav" and rate == 24000 and pcm
    adopted = adopt_resource(application, "a1", LEARNER, course_ref, demo["resource_id"], 2)
    assert adopted["state"] == "adopted"
    entry = resource(application, course_ref, demo["resource_id"])
    assert entry["adopted_version"] == 2 and len(entry["versions"]) == 2
    with pytest.raises(LookupError):
        adopt_resource(application, "a2", LEARNER, course_ref, demo["resource_id"], 3)
    assert len(application.models.calls) == calls  # Voice never goes to the model.
    assert f"resource generated course={course_ref.object_id}" in caplog.text
    assert "kind=audio version=2" in caplog.text and "resource adopted" in caplog.text
    assert demo["text"] not in caplog.text


def visual_draft(context):
    draft = science_draft(context)
    picture = draft["resources"][0] | {"required": True}
    draft["resources"] = [
        picture,
        picture
        | {
            "resource_id": "cup-motion",
            "kind": "animation",
            "required": False,
            "stage": "help",
            "purpose": "After a plea, show the water pouring out",
        },
        picture | {"resource_id": "cup-video", "kind": "video", "required": False},
    ]
    return draft


def test_image_and_animation_are_generated_by_the_model(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    confirmed = application.confirm("c1", LEARNER, course_ref)
    assert confirmed["resources"] == "awaiting_generation"
    assert resource(application, course_ref, "cup-video")["state"] == "not_connected"
    caplog.set_level(logging.INFO, logger="project_j")
    generate(application, "g1", course_ref, "cup-picture")
    picture, media_type = resource_file(application, LEARNER, course_ref, "cup-picture", 1)
    assert (picture, media_type) == (solid_png(), "image/png")
    source = resource(application, course_ref, "cup-picture")["versions"][0]["source"]
    assert source["description"] and source["call_id"]
    drawing = [call for call in application.models.calls if call["purpose"] == "preparation"][-1]
    assert drawing["failure"] is None and drawing["media"] == 1
    generate(application, "g2", course_ref, "cup-motion")
    svg, media_type = resource_file(application, LEARNER, course_ref, "cup-motion", 1)
    assert media_type == "image/svg+xml" and svg.startswith(b"<svg")
    with pytest.raises(Conflict, match="Video resources cannot be made yet"):
        generate(application, "g3", course_ref, "cup-video")
    assert "kind=image" in caplog.text and "kind=animation" in caplog.text
    assert "两个杯子" not in caplog.text and "<svg" not in caplog.text


def test_unsafe_animation_keeps_the_preview(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    application.confirm("c1", LEARNER, course_ref)
    before = preview(application, course_ref).revision
    adapter = application.models.adapters["simulated"]
    original = adapter.respond

    def respond(request):
        if request.context.get("kind") == "animation":
            return {"svg": UNSAFE, "description": "d"}
        return original(request)

    monkeypatch.setattr(adapter, "respond", respond)
    caplog.set_level(logging.INFO, logger="project_j")
    with pytest.raises(
        PreparationUnavailable,
        match=r"The resource could not be made \(The animation did not pass the check\)\. "
        "The earlier resource stays",
    ):
        generate(application, "g1", course_ref, "cup-motion")
    assert preview(application, course_ref).revision == before
    assert "reason=animation_rejected" in caplog.text and "script" not in caplog.text


def test_failed_synthesis_keeps_the_preview(application, monkeypatch):
    course_ref = prepared(application)
    application.confirm("c1", LEARNER, course_ref)
    before = preview(application, course_ref).revision

    async def fail(text, *, seed=None):
        raise SynthesisFailed("timeout")
        yield b""

    monkeypatch.setattr(application.synthesizer, "stream", fail)
    with pytest.raises(
        PreparationUnavailable, match=r"The resource could not be made \(Voice: it took too long\)"
    ):
        generate(application, "g1", course_ref, demo_of(application, course_ref)["resource_id"])
    assert preview(application, course_ref).revision == before


@pytest.mark.parametrize(
    ("failure", "text"),
    [
        ("unavailable", "the model is not available now"),
        ("quota", "the picture limit is used up or busy"),
        ("incomplete", "no picture came back"),
        ("config_mismatch", "the model used a tool other than drawing"),
        ("moderated", "the safety system blocked the picture"),
        (
            "network_blocked",
            r"the picture service refused this network \(IP not authorized\); try another network",
        ),
    ],
)
def test_failed_drawing_names_the_model_failure(application, monkeypatch, failure, text):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    application.confirm("c1", LEARNER, course_ref)
    adapter = application.models.adapters["simulated"]

    async def fail(request, binding):
        return AdapterReply(data=None, failure=failure)

    monkeypatch.setattr(adapter, "invoke", fail)
    with pytest.raises(PreparationUnavailable, match=rf"The resource could not be made \({text}\)"):
        generate(application, "g1", course_ref, "cup-picture")


def test_no_generation_during_class(application, monkeypatch):
    core = asyncio.run(start(application))
    course_ref = ref(core.bindings["course"])
    said = []
    monkeypatch.setattr(application.synthesizer, "stream", lambda text, **_: said.append(text))
    demo = next(item for item in core.course.resources if item.resource_id.startswith("demo:"))
    with pytest.raises(StorageBlocked):
        generate(application, "g9", course_ref, demo.resource_id)
    assert said == []


def test_backup_restore_keeps_the_lesson_folder(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    asyncio.run(application.confirm_course("c1", LEARNER, course_ref))
    confirm_resources(application, "r1", LEARNER, course_ref)
    lesson = lesson_of(application, LEARNER, course_ref)
    # Older previews and materials still name drafts that are gone; the backup does not care.
    backup = application.files.backup()
    restored = PrivateFiles.restore(backup, application.store.root, "restored")
    files = PrivateFiles(restored)
    assert files.read_binary(lesson.body["files"]["cup-picture"]) == solid_png()
    for entry in lesson.body["files"].values():
        files.read_binary(entry)
    files.read_asset(lesson.body["story"])
    restored.close()


def test_resource_endpoints(application, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    course_ref = science_lesson(application, monkeypatch, visual_draft)
    body = {"learner_id": LEARNER, "course_ref": course_ref.model_dump()}
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.token}"}
        application.confirm("c1", LEARNER, course_ref)
        request = body | {"request_id": "g1", "resource_id": "cup-motion"}
        assert client.post("/api/resource/generate", json=request).status_code == 401
        generated = client.post("/api/resource/generate", headers=parent, json=request)
        assert generated.status_code == 200 and generated.json()["version"] == 1
        query = {
            "learner_id": LEARNER,
            "object_id": course_ref.object_id,
            "revision": course_ref.revision,
            "resource_id": "cup-motion",
            "version": 1,
        }
        file = client.get("/api/resource/file", headers=parent, params=query)
        assert file.status_code == 200 and file.headers["content-type"] == "image/svg+xml"
        assert file.headers["cache-control"] == "no-store"
        assert file.headers["content-security-policy"] == "sandbox"
        adopt = body | {"request_id": "a1", "resource_id": "cup-motion", "version": 1}
        adopted = client.post("/api/resource/adopt", headers=parent, json=adopt)
        assert adopted.status_code == 200 and adopted.json()["state"] == "adopted"
        application.models.adapters["simulated"].fail_next = True
        failed = client.post(
            "/api/resource/generate",
            headers=parent,
            json=request | {"request_id": "g2", "resource_id": "cup-picture"},
        )
        assert failed.status_code == 503
        assert failed.json() == {
            "message": "The resource could not be made (the model is not available now). "
            "The earlier resource stays; you can try again."
        }
        assert "refused POST /api/resource/generate 503 PreparationUnavailable" in caplog.text
