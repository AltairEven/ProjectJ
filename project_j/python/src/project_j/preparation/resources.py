"""Teaching resources of a confirmed course: generate, look or listen, adopt (ADR-0017).

Voice comes from the local synthesizer; pictures and animations from the preparation model.
Each generation is a new version in the preview; adopting one makes it the version a class
uses, and earlier versions stay.
"""

import logging
import time
from contextlib import aclosing
from datetime import UTC, datetime

from project_j.application.documents import publication, successor
from project_j.contracts import RevisionRef
from project_j.platform.storage import Conflict
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.recorded import decode_wav, encode_wav
from project_j.platform.visuals import MEDIA_TYPES, checked_svg, image_type
from project_j.preparation.contracts import CourseBody
from project_j.preparation.drafting import PreparationUnavailable
from project_j.preparation.materials import (
    animation_request,
    check_request,
    image_request,
    verdicts,
)
from project_j.preparation.packages import student_text
from project_j.preparation.pictures import picture_groups

log = logging.getLogger(__name__)
GENERATED = ("audio", "image", "animation")
KIND_LABELS = {"audio": "Audio", "image": "Picture", "animation": "Animation", "video": "Video"}
SYNTHESIS_TEXT = {
    "not_ready": "the voice maker is not ready",
    "empty_text": "the text is empty",
    "too_long": "the text is too long",
    "timeout": "it took too long",
    "truncated": "it stopped too early",
    "failed": "making the voice failed",
}
SUFFIXES = {media_type: suffix for suffix, media_type in MEDIA_TYPES.items()}
# Picture failures as the parent reads them; the rest use the shared labels.
MODEL_TEXT = {
    "quota": "the picture limit is used up or busy",
    "incomplete": "no picture came back",
    "config_mismatch": "the model used a tool other than drawing",
}
# Each checked picture is drawn at most three times: the first time and two redraws.
ROUNDS = 3
# Why a checked picture was not used, as the student reads it.
CHECK_TEXT = {
    "wrong": "it does not show the right thing",
    "misleading": "it could mislead",
    "answer_text": "it writes the answer",
    "missing": "it misses what the question asks about",
    "alike": "the pictures look too alike",
    "also_fits": "more than one picture fits",
    "style": "its style does not match",
    "check_unavailable": "the check could not run",
}
NO_CHECK = "Pictures cannot be checked now, so this question stays in words."


def confirmed_package(app, learner_id: str, course_ref: RevisionRef):
    course, _, preview = app.current_package(learner_id, course_ref)
    if (preview.body.get("confirmation") or {}).get("course_ref") != course_ref.model_dump():
        raise Conflict("Confirm the lesson first; then its resources can be made.")
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


def story_of(app, learner_id: str, course: CourseBody) -> str:
    """The story as the student sees it (title and paragraphs), from the course's material."""
    material = app.store.read(course.material_refs[0], learner_id)
    return student_text(app.files.read_asset(material.body["asset"]))


async def produce(app, item: dict, course: CourseBody, story: str) -> tuple[bytes, str, dict]:
    """The file, its media type and where it came from."""
    if item["kind"] == "audio":
        pcm = bytearray()
        try:
            async with aclosing(app.synthesizer.stream(item["text"])) as stream:
                async for chunk in stream:
                    pcm += chunk
        except SynthesisFailed as error:
            text = SYNTHESIS_TEXT.get(error.reason, error.reason)
            raise Failed(f"synthesis_{error.reason}", f"Voice: {text}") from None
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
    request = (image_request if item["kind"] == "image" else animation_request)(item, course, story)
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
        raise Failed("animation_rejected", "The animation did not pass the check") from None


async def generate_resource(app, request_id, learner_id, course_ref: RevisionRef, resource_id):
    signature = {"course_ref": course_ref.model_dump(), "resource_id": resource_id}
    completed = app.ledger.completed_request(request_id, learner_id, "generate_resource", signature)
    if completed:
        return completed
    app.store.ensure_idle()  # Never while a class runs: GPU and storage stay with the class.
    course, preview = confirmed_package(app, learner_id, course_ref)
    _, item = entry(preview, resource_id)
    if item["kind"] not in GENERATED:
        raise Conflict(
            f"{KIND_LABELS[item['kind']]} resources cannot be made yet; use the fallback."
        )
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
            body = CourseBody.model_validate(course.body)
            content, media_type, source = await produce(
                app, item, body, story_of(app, learner_id, body)
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
            raise PreparationUnavailable(error.reason, "resource") from None
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
        obj = successor(
            preview,
            operation,
            preview.body | {"resources": resources},
            refs=preview.dependency_refs,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "resource",
            [obj],
            publications=(publication(obj, "A new version of the resource"),),
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
        kept = {key: value for key, value in item.items() if key != "rejected_reason"}
        resources[index] = kept | {"state": "adopted", "adopted_version": version}
        obj = successor(
            preview,
            operation,
            preview.body | {"resources": resources},
            refs=preview.dependency_refs,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "adoption",
            [obj],
            publications=(publication(obj, "This version adopted"),),
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


def update_resources(app, request_id, learner_id, course_ref: RevisionRef, changes: dict):
    """Merge changes[resource_id] into those resources of the preview: picture checks (added
    to those recorded before) and pictures turned down. Idempotent by request_id."""
    signature = {"course_ref": course_ref.model_dump(), "changes": changes}
    if app.ledger.completed_request(request_id, learner_id, "update_resources", signature):
        return
    course, preview = confirmed_package(app, learner_id, course_ref)
    operation = app.request(
        request_id,
        learner_id,
        "update_resources",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    if app.ledger.result(operation, learner_id):
        return
    if app.saved_step(operation, learner_id, "update") is None:
        resources = []
        for item in preview.body["resources"]:
            change = changes.get(item["resource_id"], {})
            if "checks" in change:
                change = change | {"checks": item.get("checks", {}) | change["checks"]}
            resources.append(item | change)
        obj = successor(
            preview,
            operation,
            preview.body | {"resources": resources},
            refs=preview.dependency_refs,
        )
        app.save_step(
            operation,
            learner_id,
            "update",
            [obj],
            publications=(publication(obj, "Picture checks recorded"),),
            baselines=(course.ref,),
        )
    app.ledger.finish(operation, learner_id, {"resource_ids": sorted(changes)})


def mark(version: int, result: str, reason: str | None) -> dict:
    """One version's check result, as update_resources records it."""
    return {"checks": {str(version): {"result": result, "reason": reason}}}


async def check_group(
    app, request_id, learner_id, course_ref: RevisionRef, group_key, versions, round_no
):
    """Let the preparation model look at these versions of one group: resource id -> why it
    failed (None: passed), recorded in the preview; None when the check could not run.
    Idempotent by request_id: a replay calls no model."""
    signature = {"course_ref": course_ref.model_dump(), "group": group_key, "versions": versions}
    completed = app.ledger.completed_request(request_id, learner_id, "check_pictures", signature)
    if completed:
        return completed["reasons"]
    course, preview = confirmed_package(app, learner_id, course_ref)
    body = CourseBody.model_validate(course.body)
    items = [entry(preview, resource_id)[1] for resource_id in versions]
    images = tuple(
        app.files.read_binary(item["versions"][versions[item["resource_id"]] - 1]["asset"])
        for item in items
    )
    operation = app.request(
        request_id,
        learner_id,
        "check_pictures",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed["reasons"]
    started = time.perf_counter()
    request = check_request(items, images, body, story_of(app, learner_id, body))
    result = await app.models.run(request)
    found = verdicts(result.output, len(items)) if result.ok else None
    elapsed = round((time.perf_counter() - started) * 1000)
    names = ",".join(versions)
    if found is None:
        log.warning(
            "picture check course=%s group=%s resources=%s round=%d result=unavailable "
            "reasons=%s elapsed_ms=%d",
            course.object_id,
            group_key,
            names,
            round_no,
            result.failure or "invalid_reply",
            elapsed,
        )
        return None
    reasons = {resource_id: found[i] for i, resource_id in enumerate(versions, 1)}
    marks = {
        resource_id: mark(versions[resource_id], "fail" if reason else "pass", reason)
        for resource_id, reason in reasons.items()
    }
    update_resources(app, f"{request_id}:record", learner_id, course_ref, marks)
    failed = sorted({reason for reason in reasons.values() if reason})
    log.info(
        "picture check course=%s group=%s resources=%s round=%d result=%s reasons=%s elapsed_ms=%d",
        course.object_id,
        group_key,
        names,
        round_no,
        "fail" if failed else "pass",
        ",".join(failed) or "none",
        elapsed,
    )
    return app.ledger.finish(operation, learner_id, {"reasons": reasons})["reasons"]


def reject(app, request_id, learner_id, course_ref, group_key, group, reason):
    """Turn a group down: a picture question goes in words, a story picture is not shown."""
    changes = {name: {"state": "rejected", "rejected_reason": reason} for name in group}
    update_resources(app, request_id, learner_id, course_ref, changes)
    log.info(
        "picture rejected course=%s group=%s reason=%s", course_ref.object_id, group_key, reason
    )


def unchecked(app, request_id, learner_id, course_ref, resource_id, version, reason):
    """A story picture used without a check: no image input, or the check could not run."""
    marks = {resource_id: mark(version, "unchecked", None)}
    update_resources(app, request_id, learner_id, course_ref, marks)
    log.info(
        "picture unchecked course=%s resource=%s reason=%s",
        course_ref.object_id,
        resource_id,
        reason,
    )
    return adopt_resource(app, f"{request_id}:adopt", learner_id, course_ref, resource_id, version)


async def make_checked(app, learner_id, course_ref: RevisionRef, group_key, group) -> bool:
    """Draw, check, redraw what failed and adopt a group, or turn it down (ADR-0026). False
    when a drawing failed: the group stays as it was, to be made again before class."""
    key = f"{course_ref.object_id}:{course_ref.revision}"
    question = group_key.startswith("task:")
    # Without image input a picture question goes in words; a story picture is drawn and used
    # unchecked, as when a check cannot run.
    seeing = app.models.sees_images("preparation")
    try:
        if question and not seeing:
            reject(app, f"auto-reject:{key}:{group_key}", learner_id, course_ref, group_key,
                   group, "no_image_input")  # fmt: skip
            return True
        versions: dict[str, int] = {}
        pending = list(group)
        for round_no in range(1, ROUNDS + 1):
            for name in pending:
                drawn = await generate_resource(
                    app, f"auto-generate:{key}:{name}:r{round_no}", learner_id, course_ref, name
                )
                versions[name] = drawn["version"]
            reasons = (
                await check_group(
                    app,
                    f"auto-check:{key}:{group_key}:r{round_no}",
                    learner_id,
                    course_ref,
                    group_key,
                    versions,
                    round_no,
                )
                if seeing
                else None
            )
            if reasons is None:
                why = "check_unavailable" if seeing else "no_image_input"
                if question:
                    reject(app, f"auto-reject:{key}:{group_key}", learner_id, course_ref,
                           group_key, group, why)  # fmt: skip
                else:
                    unchecked(app, f"auto-unchecked:{key}:{group[0]}", learner_id, course_ref,
                              group[0], versions[group[0]], why)  # fmt: skip
                return True
            pending = [name for name in group if reasons[name]]
            if not pending:
                for name in group:
                    adopt_resource(
                        app,
                        f"auto-adopt:{key}:{name}:v{versions[name]}",
                        learner_id,
                        course_ref,
                        name,
                        versions[name],
                    )
                return True
        reject(app, f"auto-reject:{key}:{group_key}", learner_id, course_ref, group_key, group,
               reasons[pending[0]])  # fmt: skip
        return True
    except PreparationUnavailable:
        return False


def group_of(preview, resource_id) -> tuple[str, list[str]] | None:
    return next(
        (
            (key, names)
            for key, names in picture_groups(preview.body["resources"])
            if resource_id in names
        ),
        None,
    )


async def use_checked(app, request_id, learner_id, course_ref, group_key, versions, resource_id):
    """The student's own choice of a checked picture: the whole group is looked at again and
    adopted only if it passes; otherwise the pictures in use stay."""
    if not app.models.sees_images("preparation"):
        if group_key.startswith("task:"):
            raise Conflict(NO_CHECK)
        return unchecked(app, f"{request_id}:unchecked", learner_id, course_ref, resource_id,
                         versions[resource_id], "no_image_input")  # fmt: skip
    reasons = await check_group(
        app, f"{request_id}:check", learner_id, course_ref, group_key, versions, 0
    )
    failed = (
        "check_unavailable"
        if reasons is None
        else next((reason for reason in reasons.values() if reason), None)
    )
    if failed:
        raise Conflict(
            f"This picture did not pass the check: {CHECK_TEXT[failed]}. The pictures in use stay."
        )
    adopted = {
        name: adopt_resource(
            app, f"{request_id}:adopt:{name}", learner_id, course_ref, name, version
        )
        for name, version in versions.items()
    }
    return adopted[resource_id]


def chosen_versions(preview, group, resource_id, version) -> dict[str, int]:
    versions = {}
    for name in group:
        _, item = entry(preview, name)
        pick = (
            version
            if name == resource_id
            else (item.get("adopted_version") or len(item.get("versions", ())))
        )
        if not pick:
            raise Conflict("Make the other pictures of this question first.")
        versions[name] = pick
    return versions


async def choose_resource(
    app, request_id, learner_id, course_ref: RevisionRef, resource_id, version
):
    """Course details "Use version": a checked picture is checked with its group first."""
    _, preview = confirmed_package(app, learner_id, course_ref)
    found = group_of(preview, resource_id)
    if found is None:
        return adopt_resource(app, request_id, learner_id, course_ref, resource_id, version)
    _, item = entry(preview, resource_id)
    if not 1 <= version <= len(item.get("versions", ())):
        raise LookupError("unknown version of this resource")
    group_key, group = found
    versions = chosen_versions(preview, group, resource_id, version)
    return await use_checked(app, request_id, learner_id, course_ref, group_key, versions,
                             resource_id)  # fmt: skip


async def remake_resource(app, request_id, learner_id, course_ref: RevisionRef, resource_id):
    """Course details "Make again": a checked picture is drawn, then checked with its group and
    adopted if it passes; anything else is only drawn, as before."""
    _, preview = confirmed_package(app, learner_id, course_ref)
    found = group_of(preview, resource_id)
    if found is None:
        return await generate_resource(app, request_id, learner_id, course_ref, resource_id)
    group_key, group = found
    if group_key.startswith("task:") and not app.models.sees_images("preparation"):
        raise Conflict(NO_CHECK)
    drawn = await generate_resource(app, f"{request_id}:make", learner_id, course_ref, resource_id)
    _, preview = confirmed_package(app, learner_id, course_ref)
    versions = chosen_versions(preview, group, resource_id, drawn["version"])
    return await use_checked(app, request_id, learner_id, course_ref, group_key, versions,
                             resource_id)  # fmt: skip


async def ready_resources(app, learner_id, course_ref: RevisionRef, on_progress=None) -> list[str]:
    """Generate and adopt every generatable resource that has no adopted version yet
    (ADR-0018: the AI adopts the latest good version); pictures that are checked go group by
    group through make_checked, and those turned down stay down. Returns the purposes of
    required resources that failed; an optional failure is only logged."""
    _, preview = confirmed_package(app, learner_id, course_ref)
    resources = {item["resource_id"]: item for item in preview.body["resources"]}
    groups = picture_groups(list(resources.values()))
    grouped = {name for _, names in groups for name in names}
    todo = [
        item
        for item in resources.values()
        if item["kind"] in GENERATED
        and not item.get("adopted_version")
        and item.get("state") != "rejected"
        and item["resource_id"] not in grouped
    ]
    waiting = [
        (key, names)
        for key, names in groups
        if not any(resources[name].get("state") == "rejected" for name in names)
        and not all(resources[name].get("adopted_version") for name in names)
    ]
    total = len(todo) + sum(len(names) for _, names in waiting)
    failed: list[str] = []
    optional = done = 0
    for item in todo:
        key = f"{course_ref.object_id}:{course_ref.revision}:{item['resource_id']}"
        try:
            made = await generate_resource(
                app, f"auto-generate:{key}", learner_id, course_ref, item["resource_id"]
            )
            adopt_resource(
                app,
                f"auto-adopt:{key}",
                learner_id,
                course_ref,
                item["resource_id"],
                made["version"],
            )
        except PreparationUnavailable:
            if item["required"]:
                failed.append(item["purpose"])
            else:
                optional += 1
        done += 1
        if on_progress:
            on_progress(done, total)
    for key, names in waiting:
        if not await make_checked(app, learner_id, course_ref, key, names):
            optional += len(names)
        done += len(names)
        if on_progress:
            on_progress(done, total)
    log.info(
        "course resources generated=%d failed=%d required_failed=%d",
        total - len(failed) - optional,
        len(failed) + optional,
        len(failed),
    )
    return failed
