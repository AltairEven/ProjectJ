import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from project_j.classroom.harness import DeliveryFeedback
from project_j.contracts import RevisionRef
from project_j.preparation.resources import adopt_resource, generate_resource

LEARNER = "fictional-L1"
CONDITIONS = {
    "materials": "reported_available",
    "time": "reported_30_minutes",
    "raw_report": "明确模拟：纸、书、小车、30分钟及陪伴均可用。",
    "explicit_start": True,
}


async def start(app, request_id="start1"):
    prepared = await app.prepare("prepare1", app.learner_id)
    await ready(app, prepared)
    result = app.start(
        request_id, app.learner_id, RevisionRef.model_validate(prepared["course_ref"]), CONDITIONS
    )
    assert result["decision"] == f"allow_{app.store.realm}"
    return app.core


def deliver(core, status="completed", delivered_range=("text_content",)):
    action = core.actions[core.pending]
    return core.delivery(
        DeliveryFeedback(
            delivery_event_id=str(uuid4()),
            delivery_id=str(uuid4()),
            action_id=action.action_id,
            activity_id=core.activity_id,
            task_instance_id=action.task_instance_id,
            sequence=1,
            output_device_id="explicit_simulation_device",
            modality="text",
            status=status,
            delivered_range=delivered_range,
            occurred_at=datetime.now(UTC),
        )
    )


def deliver_all(core):
    while core.pending:
        deliver(core)


async def answer(app, text, intent="unknown"):
    result = await app.adapter.text(str(uuid4()), text, intent)
    deliver_all(app.core)
    return result


async def read(app, event_id, text, recording_ms=4000.0, trigger="explicit"):
    """Simulated reading: the recording bytes are the text the fictional child read."""
    return await app.adapter.recording(
        event_id,
        text.encode(),
        input_started_ms=0.0,
        input_ended_ms=recording_ms + 100,
        recording_ms=recording_ms,
        completion_trigger=trigger,
    )


async def read_aloud_all(app):
    """Read every remaining segment faithfully; a no-op when no reading task is current."""
    core = app.core
    while core.reading_task() is not None and core.wait_kind in {
        "answer",
        "retry",
        "clarification",
    }:
        await read(app, str(uuid4()), core.reading_task().reference_text)
        deliver_all(core)


async def complete_story(app, helped=False, offscreen_report=True):
    core = await start(app)
    deliver_all(core)
    await answer(app, "听到了，先读故事再试纸桥。", "confirmation")
    if helped:
        await answer(app, "a car")
        await answer(app, "帮我一下", "help")
        await answer(app, "还是不懂，帮我一下", "help")
    await answer(app, "a bridge")
    await answer(app, "She folded it.")
    await answer(app, "我指的是折过的纸。", "report")
    if offscreen_report:
        core.control(str(uuid4()), "return")
        deliver_all(core)
        await answer(app, "用同一本书和同一辆车，第二次纸折了，车没有掉。", "report")
    else:
        core.control(str(uuid4()), "close")
        deliver_all(core)
    await read_aloud_all(app)
    await answer(app, "听到了，下次看不同的桥。", "confirmation")
    assert core.mode == "ended"
    fact = app.seal()
    return core, fact


async def teach(app, request_id, course_ref, answers):
    """Teach one prepared course with first-try answers and an offscreen report; seal the fact."""
    await ready(app, {"course_ref": course_ref})
    result = app.start(
        request_id, app.learner_id, RevisionRef.model_validate(course_ref), CONDITIONS
    )
    assert result["decision"] == "allow_simulation"
    core = app.core
    deliver_all(core)
    await answer(app, "听到了。", "confirmation")
    for text in answers:
        await answer(app, text)
    await answer(app, "我指的是折过的纸。", "report")
    core.control(str(uuid4()), "return")
    deliver_all(core)
    await answer(app, "第二次纸折了，车没有掉。", "report")
    await read_aloud_all(app)
    await answer(app, "听到了。", "confirmation")
    assert core.mode == "ended"
    return app.seal()


def judgments(app):
    """Classroom judgment calls only; preparation calls the model before class too."""
    return [call for call in app.models.calls if call["purpose"] == "classroom_judgment"]


async def ready(app, prepared):
    """The course version is confirmed and its required resources made and adopted; starting
    needs both. Only required ones, to keep the suite fast: confirm-and-start makes all of
    them and test_confirm_and_start covers that."""
    course_ref = prepared["course_ref"]
    if isinstance(course_ref, dict):
        course_ref = RevisionRef.model_validate(course_ref)
    key = f"{course_ref.object_id}:{course_ref.revision}"
    learner = app.learner_id
    result = app.confirm(f"confirm:{key}", learner, course_ref)
    preview = app.store.read(RevisionRef.model_validate(result["preview_ref"]), learner)
    for item in preview.body["resources"]:
        if item["required"]:
            name = item["resource_id"]
            made = await generate_resource(app, f"generate:{key}:{name}", learner, course_ref, name)
            adopt_resource(app, f"adopt:{key}:{name}", learner, course_ref, name, made["version"])
    return result


def confirmed(app, prepared):
    """ready() for synchronous tests."""
    return asyncio.run(ready(app, prepared))


async def wait_jobs(app):
    """Until no background job runs; a finishing job may start the next one."""
    while app.jobs:
        await asyncio.gather(*app.jobs.values(), return_exceptions=True)


def prepare_course(app, request_id="prepare1"):
    """A prepared course now comes from the background job; tests prepare directly."""
    return asyncio.run(app.prepare(request_id, app.learner_id))


async def next_course(app, request_id, learner_id, activity_id):
    """What the background jobs do after a lesson, with the student's decisions in between:
    evaluate, acknowledge, adopt and plan, accept a changed goal, prepare (ADR-0018)."""
    from project_j.preparation.drafting import PreparationUnavailable

    evaluation = await app.evaluate(
        f"{request_id}:evaluate", learner_id, activity_id, "after_class"
    )
    app.acknowledge(f"{request_id}:ack", learner_id, activity_id, evaluation.ref)
    adopted = await app.adopt(f"{request_id}:adopt", learner_id, evaluation)
    demand = app.current(learner_id, "demand")
    if demand.body.get("goal_confirmation") == "needed":  # The student accepts the new goal.
        await app.decide_goal(f"{request_id}:goal", learner_id, demand.ref, "accept")
    try:
        package = await app.prepare(f"{request_id}:prepare", learner_id)
    except PreparationUnavailable as gap:
        package = {"status": "not_ready", "reason": str(gap)}
    return {
        "evaluation_ref": evaluation.ref.model_dump(),
        "adoption": adopted,
        "next_package": package,
    }


def onboarding_app(tmp_path):
    """A simulation space that starts at onboarding, as a study space does."""
    from conftest import ROOT

    from project_j.application.coordinator import Coordinator
    from project_j.platform.storage import SimulationStore

    store = SimulationStore(tmp_path / "simulation", "run1")
    verification = {"result": "pass", "method": "explicit_test_fixture", "scope": "simulation_only"}
    return Coordinator(store, ROOT, verification=verification, onboarding=True)


def settle(app):
    """In TestClient tests: wait for the service thread's seal and background jobs, so the
    test thread does not write the database at the same time."""
    import time

    for _ in range(500):
        if not app.jobs and not app.pending_seals:
            return
        time.sleep(0.01)
    raise AssertionError("background work did not settle")
