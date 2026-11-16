"""The one thing the student does next, and the AI work that moves on by itself (ADR-0018).

Both are read from the saved objects, so a restart or a reload picks up where things were.
An AI job is named by what it works on; its request identity is derived from the name, so a
retry or a new process resumes the saved steps of the same operation.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial

from project_j.application.documents import identity
from project_j.contracts import RevisionRef
from project_j.curriculum.corrections import active_restrictions, correction_restrictions
from project_j.evaluation.scoring import recent_scores
from project_j.learning.adoption import review_due
from project_j.learning.contracts import FORMS, SKILLS, challenge_of
from project_j.learning.maintenance import review
from project_j.learning.onboarding import (
    QUESTIONS,
    placement_activity,
    profile,
    summarize_profile,
)
from project_j.preparation.difficulty import targets

log = logging.getLogger(__name__)
Job = tuple[str, Callable[[], Awaitable]]


def in_class(app) -> bool:
    return app.core is not None and app.core.mode != "ended"


def lesson(app, learner_id: str):
    """The course of the current demand and whether it was confirmed or taught, or None."""
    demand = app.store.current(identity(learner_id, "demand"), learner_id)
    if demand is None:
        return None
    courses = [
        doc
        for doc in app.store.list_current(learner_id, "course")
        if doc.body["demand_ref"] == demand.ref.model_dump()
    ]
    if not courses:
        return None
    course = max(courses, key=lambda doc: doc.created_at)
    taught = any(
        RevisionRef.model_validate(fact.body["course_ref"]).object_id == course.object_id
        for fact in app.store.list_current(learner_id, "fact")
    )
    confirmed = any(
        (preview.body.get("confirmation") or {}).get("course_ref") == course.ref.model_dump()
        for preview in app.store.list_current(learner_id, "preview")
    )
    return course, confirmed, taught


def restricted(app, learner_id: str, course) -> bool:
    return bool(active_restrictions(app, learner_id, (course.ref, *course.dependency_refs)))


def focus_needed(demand) -> bool:
    """A lesson's demand waits for the student's topic and ticks; a placement never asks.

    Keyed on the topic, so a demand ticked before topics existed asks again for both."""
    return (
        demand is not None
        and demand.body.get("purpose") != "placement"
        and not demand.body.get("topic")
    )


def latest_facts(app, learner_id: str):
    return sorted(
        app.store.list_current(learner_id, "fact"), key=lambda fact: fact.occurred_at, reverse=True
    )


def lesson_evaluations(app, learner_id: str):
    """Current lesson evaluations, oldest lesson first (scores read in this order, ADR-0020)."""
    return sorted(
        app.store.list_current(learner_id, "evaluation"),
        key=lambda doc: doc.occurred_at or doc.created_at,
    )


def lesson_score_summary(app, learner_id: str, use: str) -> dict:
    """The last five lessons' averages by task type and ability, for planning or preparing the
    next lesson; the log keeps counts only."""
    scores = recent_scores([doc.body for doc in lesson_evaluations(app, learner_id)])
    log.info("%s scores lessons=%d types=%d", use, scores["lessons"], len(scores["by_type"]))
    return scores


def adopted(app, learner_id: str, evaluation) -> bool:
    """Whether the current demand was planned from this evaluation."""
    demand = app.store.current(identity(learner_id, "demand"), learner_id)
    return demand is not None and evaluation.ref in demand.source_refs


def evaluation_job(app, learner_id: str) -> Job | None:
    """Evaluate a lesson when its sources changed, adopt the latest lesson's acknowledged
    evaluation, or recheck what an older lesson's acknowledged correction touched.

    Only the latest lesson plans the next one; an older one was planned from before the next
    lesson was prepared, and re-adopting it would plan backwards (design/evaluation)."""
    for index, fact in enumerate(latest_facts(app, learner_id)):
        activity = fact.body["activity_id"]
        revision, _ = app.source_revision(activity, learner_id)
        evaluation = app.store.current(identity(activity, "evaluation"), learner_id)
        if evaluation is None and index:
            continue  # An older class that was never evaluated planned nothing.
        if evaluation is None or evaluation.source_revision != revision:
            name = f"evaluate:{activity}:{revision}"
            work = partial(app.evaluate, f"auto:{name}", learner_id, activity, "after_class")
        elif not app.acknowledged(learner_id, activity):
            continue
        elif index == 0:
            if adopted(app, learner_id, evaluation):
                continue
            name = f"adopt:{evaluation.object_id}:{evaluation.revision}"
            work = partial(app.adopt, f"auto:{name}", learner_id, evaluation)
        elif correction_restrictions(app, learner_id, activity)[0]:
            name = f"recheck:{evaluation.object_id}:{evaluation.revision}"
            work = partial(app.recheck, f"auto:{name}", learner_id, evaluation)
        else:
            continue
        if not app.ledger.done(f"auto:{name}"):
            return name, work
    return None


def summary_job(app, learner_id: str) -> Job | None:
    """After the placement lesson is evaluated, the profile is written up for the student."""
    current = profile(app, learner_id)
    if current is None or current.body.get("summarized") or current.body.get("confirmed"):
        return None
    placed = placement_activity(app, learner_id)
    if placed is None or placed[1] is None:
        return None
    activity, evaluation = placed
    if evaluation.source_revision != app.source_revision(activity, learner_id)[0]:
        return None
    name = f"summarize:{activity}"
    if app.ledger.done(f"auto:{name}"):
        return None
    return name, lambda: summarize_profile(app, f"auto:{name}", learner_id, activity)


def next_job(app, learner_id: str) -> Job | None:
    found = evaluation_job(app, learner_id) or summary_job(app, learner_id)
    if found is not None:
        return found
    demand = app.store.current(identity(learner_id, "demand"), learner_id)
    waiting = demand is not None and demand.body.get("goal_confirmation") == "needed"
    if (
        demand is not None
        and not waiting
        and not focus_needed(demand)
        and lesson(app, learner_id) is None
    ):
        name = f"prepare:{demand.object_id}:{demand.revision}"
        if not app.ledger.done(f"auto:{name}"):
            return name, lambda: app.prepare(f"auto:{name}", learner_id)
    plans = app.store.list_current(learner_id, "plan")
    for due in review_due(plans, datetime.now(UTC)):
        name = f"review:{due['level']}:{due['plan_ref']['revision']}"
        if not app.ledger.done(f"auto:{name}"):
            return name, lambda level=due["level"], name=name: review(
                app, f"auto:{name}", learner_id, level
            )
    return None


def run(app, learner_id: str, name: str, work: Callable[[], Awaitable]) -> None:
    """Run work as the background job name; failure keeps its reason for a retry."""

    async def job():
        started = time.perf_counter()
        log.info("job start name=%s", name)
        try:
            await work()
        except Exception as error:  # Any failure stays visible and waits for a retry.
            app.job_failures[name] = str(error) or type(error).__name__
            log.warning(
                "job failed name=%s error=%s elapsed_ms=%.0f",
                name,
                type(error).__name__,
                (time.perf_counter() - started) * 1000,
            )
        else:
            log.info(
                "job done name=%s elapsed_ms=%.0f", name, (time.perf_counter() - started) * 1000
            )
        finally:
            app.jobs.pop(name, None)
            app.job_progress.pop(name, None)
        advance(app, learner_id)

    app.jobs[name] = asyncio.get_running_loop().create_task(job())


def advance(app, learner_id: str) -> str | None:
    """Start the next AI job unless a class runs, a job runs, or it failed and waits."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None  # Called outside the service loop; the next page load moves on.
    if in_class(app) or app.jobs:
        return None
    found = next_job(app, learner_id)
    if found is None or found[0] in app.job_failures:
        return None
    run(app, learner_id, *found)
    return found[0]


def retry(app, learner_id: str, name: str) -> str | None:
    app.job_failures.pop(name, None)
    return advance(app, learner_id)


def jobs(app) -> list[dict]:
    running = [
        {"name": name, "state": "running", "reason": None, "progress": app.job_progress.get(name)}
        for name in app.jobs
    ]
    failed = [
        {"name": name, "state": "failed", "reason": reason, "progress": None}
        for name, reason in app.job_failures.items()
    ]
    return running + failed


def onboarding_step(app, learner_id: str) -> dict | None:
    """Onboarding comes first until the student confirmed the profile; None while the AI
    works on it, or once it is done."""
    current = profile(app, learner_id)
    if current is not None and current.body.get("confirmed"):
        return None
    if current is None or not current.body.get("lexile"):
        return {"kind": "lexile", "subject": {}}
    if not current.body.get("chat"):
        return {"kind": "chat", "subject": {"questions": list(QUESTIONS)}}
    placed = placement_activity(app, learner_id)
    if placed is None:
        prepared = lesson(app, learner_id)
        if prepared is None:
            return None
        course, confirmed, _ = prepared
        return {
            "kind": "placement",
            "subject": {"course_ref": course.ref.model_dump(), "confirmed": confirmed},
        }
    activity, evaluation = placed
    if evaluation is not None and current.body.get("summarized"):
        return {
            "kind": "profile",
            "subject": {"activity_id": activity, "evaluation_ref": evaluation.ref.model_dump()},
        }
    return None


def retake_course(app, learner_id: str, fact) -> dict | None:
    """A class the student stopped, or the service cut, may be taken again from the start while
    its evaluation waits: nothing has been replanned, so the same course and its made resources
    still fit. The earlier class stays in the history."""
    if (fact.body.get("end") or {}).get("reason") not in {"stop", "interrupted"}:
        return None
    ref = RevisionRef.model_validate(fact.body["course_ref"])
    course = app.store.current(ref.object_id, learner_id)
    demand = app.store.current(identity(learner_id, "demand"), learner_id)
    if course is None or course.ref != ref or demand is None:
        return None
    return ref.model_dump() if course.body["demand_ref"] == demand.ref.model_dump() else None


def next_step(app, learner_id: str) -> dict:
    """The one thing for the student, in the order of the main page (design/interface)."""
    if in_class(app):
        return {"kind": "class", "subject": {"activity_id": app.core.activity_id}}
    onboarding = onboarding_step(app, learner_id)
    if onboarding is not None:
        return onboarding
    documents = app.store.list_current(learner_id)
    if app.pending_seals or app.lost_activities(learner_id, documents):
        return {"kind": "interrupted", "subject": {}}
    for doc in documents:
        if doc.kind == "decision" and doc.body.get("decision_state") == "pending":
            return {"kind": "decision", "subject": {"decision_ref": doc.ref.model_dump()}}
    placed = placement_activity(app, learner_id)
    for index, fact in enumerate(latest_facts(app, learner_id)):
        activity = fact.body["activity_id"]
        evaluation = app.store.current(identity(activity, "evaluation"), learner_id)
        current_source = evaluation is not None and (
            evaluation.source_revision == app.source_revision(activity, learner_id)[0]
        )
        placement = placed is not None and placed[0] == activity  # accepted with the profile
        if not current_source or placement or app.acknowledged(learner_id, activity):
            continue
        # Written again after the student corrected an acknowledged version.
        corrected = app.store.current(identity(activity, "acknowledgement"), learner_id)
        if index and corrected is None:
            continue  # An older class the student never acknowledged waits on its page.
        subject = {"activity_id": activity, "evaluation_ref": evaluation.ref.model_dump()}
        if corrected is not None:
            subject["corrected"] = True
        if index == 0 and (retake := retake_course(app, learner_id, fact)):
            subject["retake"] = retake
        return {"kind": "evaluation", "subject": subject}
    demand = app.store.current(identity(learner_id, "demand"), learner_id)
    if demand is not None and demand.body.get("goal_confirmation") == "needed":
        return {
            "kind": "goal",
            "subject": {
                "demand_ref": demand.ref.model_dump(),
                "primary_goal": demand.body["primary_goal"],
                "auxiliary_goal": demand.body["auxiliary_goal"],
                "goal_reason": demand.body.get("goal_reason"),
                "proposed_capabilities": demand.body.get("proposed_capabilities", []),
            },
        }
    # Not while a newer evaluation still has to plan this demand again (2026-11-09).
    if (
        focus_needed(demand)
        and lesson(app, learner_id) is None
        and evaluation_job(app, learner_id) is None
    ):
        account = app.store.current(identity(learner_id, "profile"), learner_id)
        account = account.body if account else {}
        return {
            "kind": "focus",
            "subject": {
                "demand_ref": demand.ref.model_dump(),
                "options": [
                    {"skill_id": skill, "group": group, "label": label}
                    for skill, (group, label, _) in SKILLS.items()
                ],
                "suggestions": list(demand.body.get("focus_suggestions", [])),
                "topics": [
                    item | {"form_label": FORMS.get(item.get("form"), (None,))[0]}
                    for item in demand.body.get("topic_suggestions", [])
                ],
                "challenge": challenge_of(account),  # How hard the story is (ADR-0023).
                "targets": targets(account.get("lexile")),
            },
        }
    starting = [
        row for row in jobs(app) if row["name"].startswith(("confirm:", "start:", "remake:"))
    ]
    if starting:  # Confirmed: resources are made and adopted; start: the class then opens.
        return {"kind": "resources", "subject": starting[0]}
    current = lesson(app, learner_id)
    # A course whose basis a correction restricted waits until it is rechecked.
    if current is not None and not current[2] and not restricted(app, learner_id, current[0]):
        course, confirmed, _ = current
        return {
            "kind": "course",
            "subject": {"course_ref": course.ref.model_dump(), "confirmed": confirmed},
        }
    if app.jobs or app.job_failures:
        return {"kind": "waiting", "subject": {"failed": list(app.job_failures)}}
    return {"kind": "none", "subject": {}}
