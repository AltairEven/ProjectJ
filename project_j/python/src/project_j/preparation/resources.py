"""Teaching resources of a confirmed course: generate, look or listen, adopt (ADR-0017).

Voice comes from the local synthesizer; pictures and animations from the preparation model.
Each generation is a new version in the preview; adopting one makes it the version a class
uses, and earlier versions stay.
"""

import logging
import time
from contextlib import aclosing
from datetime import UTC, datetime

from project_j.application.documents import document
from project_j.contracts import RevisionRef
from project_j.platform.storage import Conflict, Publication
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.recorded import decode_wav, encode_wav
from project_j.platform.visuals import MEDIA_TYPES, checked_svg, image_type
from project_j.preparation.contracts import CourseBody
from project_j.preparation.drafting import PreparationUnavailable
from project_j.preparation.materials import animation_request, image_request

log = logging.getLogger(__name__)
GENERATED = ("audio", "image", "animation")
KIND_LABELS = {"audio": "语音", "image": "图片", "animation": "动画", "video": "视频"}
SYNTHESIS_TEXT = {
    "not_ready": "合成器未就绪",
    "empty_text": "文字为空",
    "too_long": "文字过长",
    "timeout": "超时",
    "truncated": "未正常结束",
    "failed": "合成失败",
}
SUFFIXES = {media_type: suffix for suffix, media_type in MEDIA_TYPES.items()}
# Picture failures as the parent reads them; the rest use the shared labels.
MODEL_TEXT = {
    "quota": "出图额度不足或限流",
    "incomplete": "没有取得图片",
    "config_mismatch": "模型用了出图以外的工具",
}


def confirmed_package(app, learner_id: str, course_ref: RevisionRef):
    course, _, preview = app.current_package(learner_id, course_ref)
    if (preview.body.get("confirmation") or {}).get("course_ref") != course_ref.model_dump():
        raise Conflict("课程未确认：确认后才能生成资源。")
    return course, preview


def entry(preview, resource_id: str) -> tuple[int, dict]:
    for index, item in enumerate(preview.body["resources"]):
        if item["resource_id"] == resource_id:
            return index, item
    raise LookupError("unknown resource of this course version")


class Failed(Exception):
    """A generation that produced nothing usable: log code and parent-facing reason."""

    def __init__(self, code: str, reason: str):
        super().__init__(code)
        self.code, self.reason = code, reason


async def produce(app, item: dict, course: CourseBody) -> tuple[bytes, str, dict]:
    """The file, its media type and where it came from."""
    if item["kind"] == "audio":
        pcm = bytearray()
        try:
            async with aclosing(app.synthesizer.stream(item["text"])) as stream:
                async for chunk in stream:
                    pcm += chunk
        except SynthesisFailed as error:
            text = SYNTHESIS_TEXT.get(error.reason, error.reason)
            raise Failed(f"synthesis_{error.reason}", f"语音合成：{text}") from None
        rate = app.synthesizer.sample_rate
        return (
            encode_wav(bytes(pcm), rate),
            "audio/wav",
            {
                "service": app.synthesizer.service,
                "version": app.synthesizer.version,
                "model": app.synthesizer.model,
                "voice": app.synthesizer.voice,
                "sample_rate": rate,
                "audio_seconds": round(len(pcm) / 2 / rate, 2),
            },
        )
    request = (image_request if item["kind"] == "image" else animation_request)(item, course)
    result = await app.models.run(request)
    if not result.ok:
        drawing = MODEL_TEXT.get(result.failure) if item["kind"] == "image" else None
        raise Failed(result.failure, drawing or result.failure)
    source = {
        "call_id": result.call_id,
        "binding": result.binding.model_dump() if result.binding else None,
        "description": result.output.description,
    }
    if item["kind"] == "image":
        return result.media[0], MEDIA_TYPES[image_type(result.media[0])], source
    try:
        return checked_svg(result.output.svg), MEDIA_TYPES["svg"], source
    except ValueError:
        raise Failed("animation_rejected", "动画核对未通过") from None


async def generate_resource(app, request_id, learner_id, course_ref: RevisionRef, resource_id):
    signature = {"course_ref": course_ref.model_dump(), "resource_id": resource_id}
    completed = app.ledger.completed_request(request_id, learner_id, "generate_resource", signature)
    if completed:
        return completed
    app.store.ensure_idle()  # Never while a class runs: GPU and storage stay with the class.
    course, preview = confirmed_package(app, learner_id, course_ref)
    _, item = entry(preview, resource_id)
    if item["kind"] not in GENERATED:
        raise Conflict(f"{KIND_LABELS[item['kind']]}资源尚未接入生成，请按备用办法准备。")
    operation = app.request(
        request_id,
        learner_id,
        "generate_resource",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed
    saved = app.saved_step(operation, learner_id, "resource")
    if saved is None:
        started = time.perf_counter()
        try:
            content, media_type, source = await produce(
                app, item, CourseBody.model_validate(course.body)
            )
        except Failed as error:
            log.warning(
                "resource generation failed course=%s resource=%s kind=%s reason=%s elapsed_ms=%d",
                course.object_id,
                resource_id,
                item["kind"],
                error.code,
                round((time.perf_counter() - started) * 1000),
            )
            raise PreparationUnavailable(error.reason, "资源") from None
        version = {
            "version": len(item["versions"]) + 1,
            "asset": app.files.binary_asset(content, SUFFIXES[media_type]),
            "media_type": media_type,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": source,
        }
        index, _ = entry(preview, resource_id)
        resources = list(preview.body["resources"])
        resources[index] = item | {"state": "generated", "versions": [*item["versions"], version]}
        obj = document(
            "preview",
            preview.object_id,
            operation,
            learner_id,
            preview.body | {"resources": resources},
            refs=preview.dependency_refs,
            revision=preview.revision + 1,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "resource",
            [obj],
            publications=(
                Publication(
                    ref=obj.ref,
                    expected_revision=preview.revision,
                    reason="生成资源新版本；家长查看后才采用",
                ),
            ),
            baselines=(course.ref,),
        )
        log.info(
            "resource generated course=%s resource=%s kind=%s version=%d bytes=%d elapsed_ms=%d",
            course.object_id,
            resource_id,
            item["kind"],
            version["version"],
            len(content),
            round((time.perf_counter() - started) * 1000),
        )
    _, generated = entry(saved[0], resource_id)
    return app.ledger.finish(
        operation,
        learner_id,
        {
            "course_ref": course.ref.model_dump(),
            "preview_ref": saved[0].ref.model_dump(),
            "resource_id": resource_id,
            "version": len(generated["versions"]),
            "state": "generated",
        },
    )


def adopt_resource(app, request_id, learner_id, course_ref: RevisionRef, resource_id, version):
    signature = {
        "course_ref": course_ref.model_dump(),
        "resource_id": resource_id,
        "version": version,
    }
    completed = app.ledger.completed_request(request_id, learner_id, "adopt_resource", signature)
    if completed:
        return completed
    course, preview = confirmed_package(app, learner_id, course_ref)
    index, item = entry(preview, resource_id)
    if not 1 <= version <= len(item.get("versions", ())):
        raise LookupError("unknown version of this resource")
    operation = app.request(
        request_id,
        learner_id,
        "adopt_resource",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed
    saved = app.saved_step(operation, learner_id, "adoption")
    if saved is None:
        resources = list(preview.body["resources"])
        resources[index] = item | {"state": "adopted", "adopted_version": version}
        obj = document(
            "preview",
            preview.object_id,
            operation,
            learner_id,
            preview.body | {"resources": resources},
            refs=preview.dependency_refs,
            revision=preview.revision + 1,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "adoption",
            [obj],
            publications=(
                Publication(
                    ref=obj.ref,
                    expected_revision=preview.revision,
                    reason="家长查看后采用这一版本",
                ),
            ),
            baselines=(course.ref,),
        )
        log.info(
            "resource adopted course=%s resource=%s version=%d",
            course.object_id,
            resource_id,
            version,
        )
    return app.ledger.finish(
        operation,
        learner_id,
        {
            "course_ref": course.ref.model_dump(),
            "preview_ref": saved[0].ref.model_dump(),
            "resource_id": resource_id,
            "version": version,
            "state": "adopted",
        },
    )


def resource_file(app, learner_id, course_ref: RevisionRef, resource_id, version):
    """One generated version's file and media type, checked against its digest."""
    _, _, preview = app.current_package(learner_id, course_ref)
    _, item = entry(preview, resource_id)
    versions = item.get("versions", ())
    if not 1 <= version <= len(versions):
        raise LookupError("unknown version of this resource")
    chosen = versions[version - 1]
    return app.files.read_binary(chosen["asset"]), chosen["media_type"]


def adopted(app, preview) -> tuple[dict[str, bytes], dict[str, tuple[bytes, str]], list[str], int]:
    """What a class may use from a confirmed preview: recorded speech by its exact text and
    student visuals by resource id; the required ones without a usable adopted version; and
    how many adopted versions were unusable (file changed, or another voice or sample rate)."""
    recorded, visuals, missing, skipped = {}, {}, [], 0
    for item in preview.body["resources"]:
        content = None
        if version := item.get("adopted_version"):
            chosen = item["versions"][version - 1]
            try:
                data = app.files.read_binary(chosen["asset"])
                if item["kind"] == "audio":
                    rate, data = decode_wav(data)
                    if (rate, chosen["source"]["voice"]) != (
                        app.synthesizer.sample_rate,
                        app.synthesizer.voice,
                    ):
                        raise ValueError("recorded with another voice configuration")
                content = data
            except ValueError:
                skipped += 1
        if content is None:
            if item["required"]:
                missing.append(item["purpose"])
        elif item["kind"] == "audio":
            recorded[item["text"]] = content
        elif item["audience"] == "student":
            visuals[item["resource_id"]] = (content, chosen["media_type"])
    return recorded, visuals, missing, skipped
