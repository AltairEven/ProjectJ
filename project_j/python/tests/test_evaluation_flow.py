"""ADR-0018: the class end evaluates by itself; the student notes, objects, then acknowledges."""

import asyncio
import logging

import pytest
from support import LEARNER, complete_story, wait_jobs

from project_j.application.documents import identity
from project_j.application.progress import advance, next_step
from project_j.platform.storage import Conflict


async def evaluated(app):
    """A taught lesson whose after-class evaluation ran by itself."""
    _, fact = await complete_story(app)
    advance(app, LEARNER)
    await wait_jobs(app)
    return fact.body["activity_id"]


def current_evaluation(app, activity):
    return app.store.current(identity(activity, "evaluation"), LEARNER)


def test_class_end_evaluates_without_any_report(application):
    async def scenario():
        activity = await evaluated(application)
        step = next_step(application, LEARNER)
        assert step["kind"] == "evaluation"
        assert step["subject"]["activity_id"] == activity
        evaluation = current_evaluation(application, activity)
        assert step["subject"]["evaluation_ref"] == evaluation.ref.model_dump()
        assert not application.store.list_current(LEARNER, "feedback")

    asyncio.run(scenario())


def test_a_note_re_evaluates_and_waits_again(application):
    async def scenario():
        activity = await evaluated(application)
        first = current_evaluation(application, activity)
        application.feedback("note1", LEARNER, activity, "第二题我查了词典。")
        advance(application, LEARNER)
        await wait_jobs(application)
        second = current_evaluation(application, activity)
        assert second.revision == first.revision + 1
        step = next_step(application, LEARNER)
        assert step["kind"] == "evaluation"
        assert step["subject"]["evaluation_ref"] == second.ref.model_dump()

    asyncio.run(scenario())


def test_an_objection_becomes_a_correction_source(application):
    async def scenario():
        activity = await evaluated(application)
        text = "异议：第一题我答的是对的。"
        application.feedback("objection1", LEARNER, activity, text, ({"note": text},))
        advance(application, LEARNER)
        await wait_jobs(application)
        evaluation = current_evaluation(application, activity)
        assert all(task["working_judgment"] == "unknown" for task in evaluation.body["tasks"])
        request = [c for c in application.evaluation_calls if c["activity_id"] == activity]
        assert request[-1]["source_revision"] == evaluation.source_revision

    asyncio.run(scenario())


def test_acknowledging_a_stale_evaluation_is_refused(application):
    async def scenario():
        activity = await evaluated(application)
        old = current_evaluation(application, activity)
        application.feedback("note1", LEARNER, activity, "补记：读第二遍时看了提示。")
        advance(application, LEARNER)
        await wait_jobs(application)
        with pytest.raises(Conflict, match="请查看新版本"):
            application.acknowledge("ack-old", LEARNER, activity, old.ref)
        assert next_step(application, LEARNER)["kind"] == "evaluation"

    asyncio.run(scenario())


def test_acknowledgement_adopts_and_plans_then_prepares(application):
    async def scenario():
        activity = await evaluated(application)
        evaluation = current_evaluation(application, activity)
        application.acknowledge("ack1", LEARNER, activity, evaluation.ref)
        assert application.acknowledged(LEARNER, activity).body["acknowledged_ref"] == (
            evaluation.ref.model_dump()
        )
        advance(application, LEARNER)
        await wait_jobs(application)
        demand = application.current(LEARNER, "demand")
        assert evaluation.ref in demand.source_refs
        assert next_step(application, LEARNER)["kind"] == "focus"  # the student ticks first
        application.choose_focus("f1", LEARNER, demand.ref, [], None)
        advance(application, LEARNER)
        await wait_jobs(application)
        assert next_step(application, LEARNER)["kind"] == "course"
        # Acknowledging again is the same request, not a second adoption.
        again = application.acknowledge("ack1", LEARNER, activity, evaluation.ref)
        assert again["acknowledged_ref"] == evaluation.ref.model_dump()

    asyncio.run(scenario())


def test_flow_logs_metadata_only(application, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        activity = await evaluated(application)
        text = "异议：我觉得第一题没错。"
        application.feedback("objection1", LEARNER, activity, text, ({"note": text},))
        advance(application, LEARNER)
        await wait_jobs(application)
        evaluation = current_evaluation(application, activity)
        application.acknowledge("ack1", LEARNER, activity, evaluation.ref)
        return activity, text

    activity, text = asyncio.run(scenario())
    assert f"feedback saved activity={activity} objection=true" in caplog.text
    assert f"evaluation acknowledged activity={activity} revision=" in caplog.text
    assert text not in caplog.text
