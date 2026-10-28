"""Where a course's files live: its draft until the resources are confirmed, then its lesson
folder named by category, number and date (ADR-0027)."""

from datetime import date, datetime

from project_j.application.documents import identity
from project_j.contracts import Document, RevisionRef
from project_j.platform.files import DRAFTS


def local_today() -> date:
    return datetime.now().astimezone().date()


def draft_of(store, learner_id: str, course_object_id: str) -> str:
    """The course's draft, named by the day its first version was prepared."""
    try:
        first = store.read(RevisionRef(object_id=course_object_id, revision=1), learner_id)
        day = first.created_at.astimezone().date()
    except LookupError:  # Being prepared now: not saved yet.
        day = local_today()
    return f"{DRAFTS}/{day.isoformat()}-{course_object_id[:8]}"


def story_path(draft: str, revision: int) -> str:
    return f"{draft}/story.r{revision}.md"


def version_path(draft: str, kind: str, stem: str, version: int, suffix: str) -> str:
    return f"{draft}/{'audio' if kind == 'audio' else 'pictures'}/{stem}.v{version}.{suffix}"


def next_number(lessons: list[dict], category: str) -> int:
    """Numbers count per category from 1; only confirmed lessons hold one."""
    return 1 + max((body["number"] for body in lessons if body["category"] == category), default=0)


def lesson_identity(course_ref: RevisionRef) -> str:
    return identity(f"{course_ref.object_id}:{course_ref.revision}", "lesson")


def lesson_of(app, learner_id: str, course_ref: RevisionRef) -> Document | None:
    """The confirmed lesson of this course version, once its resources are confirmed."""
    return app.store.current(lesson_identity(course_ref), learner_id)
