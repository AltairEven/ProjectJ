from datetime import UTC, datetime
from uuid import uuid4

from project_j.classroom.harness import DeliveryFeedback
from project_j.contracts import RevisionRef

LEARNER = "fictional-L1"
CONDITIONS = {
    "materials": "reported_available",
    "time": "reported_30_minutes",
    "accompaniment": "reported_present",
    "raw_report": "明确模拟：纸、书、小车、30分钟及陪伴均可用。",
    "explicit_start": True,
}


def start(app, request_id="start1"):
    prepared = app.prepare("prepare1", LEARNER)
    result = app.start(
        request_id, LEARNER, RevisionRef.model_validate(prepared["course_ref"]), CONDITIONS
    )
    assert result["decision"] == "allow_simulation"
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


async def complete_story(app, helped=False, offscreen_report=True):
    core = start(app)
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
    await answer(app, "听到了，下次看不同的桥。", "confirmation")
    assert core.mode == "ended"
    fact = app.seal()
    return core, fact
