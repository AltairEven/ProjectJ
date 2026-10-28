"""Confirming a course's resources: the adopted versions go into its lesson folder, the lesson
record lists them, and the draft goes. Classes read only from this record (ADR-0027)."""

import json
import logging
import time
from datetime import UTC, datetime

from project_j.application.documents import document, publication, successor
from project_j.contracts import RevisionRef
from project_j.platform.files import LESSONS, stems
from project_j.platform.storage import Conflict
from project_j.platform.tts.recorded import decode_wav
from project_j.preparation.contracts import CourseBody
from project_j.preparation.folders import (
    draft_of,
    lesson_identity,
    lesson_of,
    local_today,
    next_number,
    version_path,
)
from project_j.preparation.pictures import picture_groups
from project_j.preparation.resources import (
    confirmed_package,
    items_by_id,
    ready_resources,
    resource_doc,
    resource_docs,
)

log = logging.getLogger(__name__)
# The folder a confirmation chose, kept in the draft so a retry past midnight keeps it.
MARKER = "lesson.txt"


def readable(app, kind: str, manifest: dict, source: dict) -> bytes | None:
    """The file's bytes (audio as PCM), or None when it is missing, changed, or recorded with
    another voice or sample rate (ADR-0017)."""
    try:
        data = app.files.read_binary(manifest)
        if kind == "audio":
            rate, data = decode_wav(data)
            if (rate, source["voice"]) != (app.synthesizer.sample_rate, app.synthesizer.voice):
                return None
        return data
    except ValueError:
        return None


def lesson_resources(app, course: CourseBody, lesson) -> tuple[dict, dict, list[str], list[str]]:
    """Recorded speech by its exact text, student visuals by id, the purposes of required
    resources without a usable file, and the ids whose confirmed file no longer works."""
    recorded, visuals, missing, broken = {}, {}, [], []
    for spec in course.resources:
        entry = lesson.body["files"].get(spec.resource_id)
        content = readable(app, spec.kind, entry, entry["source"]) if entry else None
        if entry and content is None:
            broken.append(spec.resource_id)
        if content is None:
            if spec.required:
                missing.append(spec.purpose)
        elif spec.kind == "audio":
            recorded[spec.text] = content
        elif spec.audience == "student":
            visuals[spec.resource_id] = (content, entry["media_type"])
    return recorded, visuals, missing, broken


def index_text(course: CourseBody, folder: str, files: dict, rejected: dict) -> str:
    """The folder's list: each file, what it is for, and its words or picture description."""
    lines = [f"# {folder.split('/')[-1]}", ""]
    lines += ["- `story.md`: the story", "- `course.json`: the lesson and its brief"]
    for spec in course.resources:
        if entry := files.get(spec.resource_id):
            said = spec.text or (entry["source"] or {}).get("description") or ""
            name = entry["path"].removeprefix(f"{folder}/")
            lines.append(f"- `{name}`: {spec.purpose}" + (f": {said}" if said else ""))
        elif spec.resource_id in rejected:
            lines.append(
                f"- {spec.resource_id}: {spec.purpose}: not used ({rejected[spec.resource_id]})"
            )
    return "\n".join(lines) + "\n"


def result(lesson) -> dict:
    return {
        "course_ref": lesson.body["course_ref"],
        "lesson_ref": lesson.ref.model_dump(),
        "folder": lesson.body["folder"],
        "number": lesson.body["number"],
        "state": "confirmed",
    }


def drop_draft(app, course_object_id: str, draft: str):
    removed = app.files.remove_tree(draft)
    log.info("lesson drafts removed course=%s files=%d", course_object_id, removed)


def place(app, learner_id, body: CourseBody, draft: str, lesson) -> dict:
    """The lesson's folder, category, number and date: the confirmed lesson's, the ones this
    confirmation chose before a failure, or the next number of its category today."""
    if lesson:
        return lesson.body
    marker = app.files.path(f"{draft}/{MARKER}")
    if marker.is_file():
        return json.loads(marker.read_text())
    category = app.store.read(body.demand_ref, learner_id).body.get("purpose", "lesson")
    done = [doc.body for doc in app.store.list_current(learner_id, "lesson")]
    number, today = next_number(done, category), local_today().isoformat()
    folder = f"{LESSONS}/{category}-{number:03d}-{today}"
    chosen = {"folder": folder, "category": category, "number": number, "date": today}
    app.files.put(f"{draft}/{MARKER}", json.dumps(chosen))
    return chosen


def confirm_resources(
    app,
    request_id: str,
    learner_id: str,
    course_ref: RevisionRef,
    only: frozenset[str] = frozenset(),
) -> dict:
    """Confirm the adopted versions into the lesson folder; with only, replace just those
    resources of a confirmed lesson (broken ones remade, or a picture group turned down)."""
    signature = {"course_ref": course_ref.model_dump(), "only": sorted(only)}
    completed = app.ledger.completed_request(request_id, learner_id, "confirm_resources", signature)
    if completed:
        return completed
    course, _ = confirmed_package(app, learner_id, course_ref)
    draft = draft_of(app.store, learner_id, course.object_id)
    lesson = lesson_of(app, learner_id, course_ref)
    if lesson and not only:  # Already confirmed: a leftover draft still goes.
        if app.files.path(draft).exists():
            drop_draft(app, course.object_id, draft)
        return result(lesson)
    if only and lesson is None:
        raise Conflict("Confirm the resources first.")
    started = time.perf_counter()
    body = CourseBody.model_validate(course.body)
    docs = {doc.body["resource_id"]: doc for doc in resource_docs(app, learner_id, course_ref)}
    targets = [spec.resource_id for spec in body.resources if not only or spec.resource_id in only]
    chosen, missing = {}, []
    for resource_id in targets:
        item = docs[resource_id].body
        # A turned-down resource is not used, even with a version adopted before.
        adopted = item.get("adopted_version") and item.get("state") != "rejected"
        version = item["versions"][item["adopted_version"] - 1] if adopted else None
        if version and readable(app, item["kind"], version["asset"], version["source"]) is not None:
            chosen[resource_id] = (version, app.files.read_binary(version["asset"]))
        elif item["required"]:
            missing.append(item["purpose"])
    if missing:
        raise Conflict(
            f"Resources not ready: {', '.join(missing)}. Make and adopt them, then confirm."
        )
    operation = app.request(
        request_id,
        learner_id,
        "confirm_resources",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed
    saved = app.saved_step(operation, learner_id, "lesson")
    if saved is None:
        placed = place(app, learner_id, body, draft, lesson)
        folder = placed["folder"]
        files = dict(lesson.body["files"]) if lesson else {}
        rejected = dict(lesson.body["rejected"]) if lesson else {}
        names = stems([spec.resource_id for spec in body.resources])
        for resource_id in targets:
            item, old = docs[resource_id].body, files.get(resource_id)
            if resource_id in chosen:
                version, data = chosen[resource_id]
                suffix = version["asset"]["path"].rsplit(".", 1)[1]
                group = "audio" if item["kind"] == "audio" else "pictures"
                entry = app.files.put(f"{folder}/{group}/{names[resource_id]}.{suffix}", data)
                files[resource_id] = entry | {
                    "media_type": version["media_type"],
                    "source": version["source"],
                    "version": version["version"],
                }
                rejected.pop(resource_id, None)
            elif item.get("state") == "rejected":
                files.pop(resource_id, None)
                rejected[resource_id] = item.get("rejected_reason")
            else:
                continue
            if old and old["path"] != files.get(resource_id, {}).get("path"):
                app.files.remove(old["path"])
        if lesson:
            story = lesson.body["story"]
        else:
            material = app.store.read(body.material_refs[0], learner_id)
            story = app.files.put(
                f"{folder}/story.md", app.files.read_asset(material.body["asset"])
            )
            brief = app.store.read(body.brief_ref, learner_id)
            text = json.dumps(
                {"course": course.body, "brief": brief.body}, ensure_ascii=False, indent=2
            )
            app.files.put(f"{folder}/course.json", text + "\n")
        app.files.put(f"{folder}/index.md", index_text(body, folder, files, rejected))
        record = {
            "status": "checked",
            "course_ref": course.ref.model_dump(),
            **{key: placed[key] for key in ("category", "number", "date", "folder")},
            "story": story,
            "files": files,
            "rejected": rejected,
        }
        refs = (course.ref, *(doc.ref for doc in docs.values()))
        obj = (
            successor(lesson, operation, record, refs=refs)
            if lesson
            else document(
                "lesson", lesson_identity(course.ref), operation, learner_id, record, refs=refs
            )
        )
        reason = "Broken resources remade" if lesson else "The student confirmed the resources"
        saved = app.save_step(
            operation,
            learner_id,
            "lesson",
            [obj],
            publications=(publication(obj, reason),),
            baselines=(course.ref,),
        )
        log.info(
            "lesson %s course=%s category=%s number=%d files=%d rejected=%d elapsed_ms=%d",
            "updated" if lesson else "confirmed",
            course.object_id,
            record["category"],
            record["number"],
            len(files),
            len(rejected),
            round((time.perf_counter() - started) * 1000),
        )
    drop_draft(app, course.object_id, draft)
    return app.ledger.finish(operation, learner_id, result(saved[0]))


def reset(app, operation, learner_id, course, lesson, broken: list[str], siblings: set[str]):
    """Broken resources go back to awaiting a version; usable pictures of their groups get their
    lesson file back into the draft as an adopted version 1, to be checked with the new ones."""
    draft = draft_of(app.store, learner_id, course.object_id)
    names = stems([item["resource_id"] for item in course.body["resources"]])
    objs = []
    for resource_id in [*broken, *sorted(siblings)]:
        doc = resource_doc(app, learner_id, course.ref, resource_id)
        item = {k: v for k, v in doc.body.items() if k not in {"checks", "rejected_reason"}}
        if resource_id in siblings:
            entry = lesson.body["files"][resource_id]
            suffix = entry["path"].rsplit(".", 1)[1]
            path = version_path(draft, item["kind"], names[resource_id], 1, suffix)
            version = {
                "version": 1,
                "asset": app.files.put(path, app.files.read_binary(entry)),
                "media_type": entry["media_type"],
                "generated_at": datetime.now(UTC).isoformat(),
                "source": entry["source"],
            }
            change = {"state": "adopted", "versions": [version], "adopted_version": 1}
        else:
            change = {"state": "awaiting_generation", "versions": [], "adopted_version": None}
        objs.append(successor(doc, operation, item | change, refs=doc.dependency_refs))
    app.save_step(
        operation,
        learner_id,
        "reset",
        objs,
        publications=tuple(publication(obj, "Broken: to be made again") for obj in objs),
        baselines=(course.ref,),
    )


async def remake_broken(
    app, request_id: str, learner_id: str, course_ref: RevisionRef, on_progress=None
) -> dict:
    """Make again only the confirmed resources whose file no longer works, and put them into
    the same lesson folder; every usable file stays as it is (ADR-0027)."""
    signature = {"course_ref": course_ref.model_dump()}
    completed = app.ledger.completed_request(request_id, learner_id, "remake_broken", signature)
    if completed:
        return completed
    started = time.perf_counter()
    course, _ = confirmed_package(app, learner_id, course_ref)
    lesson = lesson_of(app, learner_id, course_ref)
    if lesson is None:
        raise Conflict("Confirm the resources first.")
    broken = lesson_resources(app, CourseBody.model_validate(course.body), lesson)[3]
    operation = app.request(
        request_id,
        learner_id,
        "remake_broken",
        signature,
        sources=(course.ref,),
        baselines=(course.ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed
    turned_down: list[str] = []  # Broken or not, pictures of a group that is now turned down.
    if broken:
        items = items_by_id(app, learner_id, course_ref)
        groups = [
            names for _, names in picture_groups(list(items.values())) if set(names) & set(broken)
        ]
        siblings = {name for names in groups for name in names} - set(broken)
        if app.saved_step(operation, learner_id, "reset") is None:
            reset(app, operation, learner_id, course, lesson, broken, siblings)
        failed = await ready_resources(
            app, learner_id, course_ref, on_progress, tag=f"remake-{request_id}"
        )
        if failed:
            raise Conflict(f"Resources not ready: {', '.join(failed)}. You can try again.")
        items = items_by_id(app, learner_id, course_ref)
        turned_down = [
            n for n in [*broken, *sorted(siblings)] if items[n].get("state") == "rejected"
        ]
        confirm_resources(
            app, f"{request_id}:lesson", learner_id, course_ref, frozenset([*broken, *turned_down])
        )
    log.info(
        "lesson remade course=%s resources=%d rejected=%d elapsed_ms=%d",
        course.object_id,
        len(broken),
        len(turned_down),
        round((time.perf_counter() - started) * 1000),
    )
    now = lesson_of(app, learner_id, course_ref)
    return app.ledger.finish(
        operation,
        learner_id,
        {
            "course_ref": course.ref.model_dump(),
            "lesson_ref": now.ref.model_dump(),
            "remade": broken,
        },
    )
