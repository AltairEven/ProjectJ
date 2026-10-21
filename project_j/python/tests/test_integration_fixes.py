"""P1-R2 integration regressions: simulated providers and temporary stores only."""

import asyncio
import logging

import pytest
from support import (
    LEARNER,
    answer,
    complete_story,
    confirmed,
    deliver_all,
    next_course,
    ready,
)

from project_j.application.documents import document
from project_j.contracts import RevisionRef
from project_j.curriculum.corrections import release
from project_j.platform.models.contracts import Binding
from project_j.platform.models.service import ModelService
from project_j.platform.models.simulated import SIMULATED_MODEL, SimulatedAdapter
from project_j.platform.storage import Conflict, Publication, SaveKey


def ref(value):
    return RevisionRef.model_validate(value)


def test_the_start_record_keeps_the_bindings_and_voice_actually_in_use(application, caplog):
    app = application
    adapter = SimulatedAdapter()
    high = Binding(adapter="simulated", model=SIMULATED_MODEL, effort="high")
    app.models = ModelService(
        {"simulated": adapter},
        {
            "planning": high,
            "preparation": high,
            "classroom_judgment": high.model_copy(update={"effort": "low"}),
            "evaluation": high,
        },
        [adapter.report(SIMULATED_MODEL, ("low", "high"))],
    )
    app.scorer.simulated = False  # As a local scorer would report.

    async def scenario():
        prepared = await app.prepare("prepare", LEARNER)
        await ready(app, prepared)
        with caplog.at_level(logging.INFO, logger="project_j"):
            course = ref(prepared["course_ref"])
            assert app.start("start", LEARNER, course, materials_ready=True)["decision"] == (
                "allow_simulation"
            )
        bindings = app.core.bindings
        app.core.control("cleanup-stop", "stop")
        app.seal()  # The store opens again at the end boundary.
        return [
            app.store.read(ref(bindings[name]), LEARNER).body for name in ("config", "input_policy")
        ]

    config, policy = asyncio.run(scenario())
    assert config["realm"] == "simulation"
    assert config["models"]["preparation"] == {
        "adapter": "simulated",
        "model": SIMULATED_MODEL,
        "effort": "high",
        "tier": "default",
        "simulated": True,
    }
    assert config["models"]["classroom_judgment"]["effort"] == "low"
    assert config["models"]["evaluation"]["effort"] == "high"
    assert (config["pronunciation"], config["synthesis"]) == ("local", "simulated")
    assert policy["real_recording"] == "enabled"
    assert "preparation_effort" not in config and "provider" not in config
    assert "start config realm=simulation" in caplog.text
    assert "preparation=high" in caplog.text and "classroom_judgment=low" in caplog.text


def test_corrected_sources_block_new_preparation_and_saved_preparation_replay(application):
    app = application

    async def scenario():
        core, fact = await complete_story(app)
        app.feedback("feedback", LEARNER, core.activity_id, "明确模拟：无额外补充")
        result = await next_course(app, "next", LEARNER, core.activity_id)
        old_course = ref(result["next_package"]["course_ref"])
        app.feedback(
            "correction",
            LEARNER,
            core.activity_id,
            "明确模拟：家长代答R1。",
            corrections=({"task_id": "reading-fact", "note": "家长代答"},),
        )
        count = len(app.store.list_current(LEARNER, "course"))
        for request_id in ("reprepare-without-recheck", "next:prepare"):
            with pytest.raises(Conflict, match="restricted"):
                (await app.prepare(request_id, LEARNER))
        assert len(app.store.list_current(LEARNER, "course")) == count
        assert (
            app.start("blocked-old", LEARNER, old_course, materials_ready=True)["decision"]
            == "blocked"
        )
        rechecked = await next_course(app, "recheck", LEARNER, core.activity_id)
        new_course = ref(rechecked["next_package"]["course_ref"])
        await ready(app, {"course_ref": new_course})
        assert app.start("allowed-new", LEARNER, new_course, materials_ready=True)["decision"] == (
            "allow_simulation"
        )
        app.core.control("cleanup-stop", "stop")
        app.seal()
        assert app.store.read(fact.ref, LEARNER).body == fact.body

    asyncio.run(scenario())


def test_start_checks_restricted_dependencies_even_without_a_direct_course_restriction(application):
    app = application
    package = asyncio.run(app.prepare("prepare", LEARNER))
    course = ref(package["course_ref"])
    demand = app.current(LEARNER, "demand")
    operation = app.request("source-restriction", LEARNER, "test-restriction", {})
    restriction = app.restrict(operation, LEARNER, demand, "明确模拟：需求依据待复核")
    assert course not in restriction.dependency_refs
    assert app.start("blocked", LEARNER, course, materials_ready=True)["decision"] == "blocked"
    with pytest.raises(Conflict, match="restricted"):
        asyncio.run(app.prepare("prepare", LEARNER))
    release(app, "release", LEARNER, restriction.ref, "learning", "明确模拟：主责已复核原需求依据")
    assert asyncio.run(app.prepare("prepare", LEARNER)) == package
    confirmed(app, {"course_ref": course})
    assert (
        app.start("allowed", LEARNER, course, materials_ready=True)["decision"]
        == "allow_simulation"
    )
    app.core.control("cleanup-stop", "stop")
    app.seal()


@pytest.mark.parametrize("lose_completion", [False, True])
def test_reused_state_retry_keeps_newer_evidence_and_rechecks_demand(application, lose_completion):
    app = application

    async def scenario():
        first, _ = await complete_story(app, helped=True)
        app.feedback("fb1", LEARNER, first.activity_id, "明确模拟：无额外补充")
        evaluation1 = await app.evaluate("eval1", LEARNER, first.activity_id, "final_feedback")
        await app.adopt("initial-adopt", LEARNER, evaluation1)
        reused = app.current(LEARNER, "state").ref
        original = app.save_step

        def fail_demand(*args, **kwargs):
            if args[2] == "demand":
                app.store.fail_at = "before_commit"
            return original(*args, **kwargs)

        app.save_step = fail_demand
        with pytest.raises(OSError):
            await app.adopt("reused-state-adopt", LEARNER, evaluation1)
        app.save_step = original
        operation = app.request(
            "reused-state-adopt",
            LEARNER,
            "adopt",
            {"evaluation_ref": evaluation1.ref.model_dump()},
            sources=(evaluation1.ref,),
            source_revision=evaluation1.source_revision,
        )
        state_step = next(
            row["payload"]
            for row in app.ledger.steps(operation, LEARNER)
            if row["step_id"] == "state"
        )
        assert state_step["execution"] == "complete"
        assert state_step["effect_refs"] == [reused.model_dump()]

        demand = app.current(LEARNER, "demand")
        app.choose_focus("newer-focus", LEARNER, demand.ref, "桥", [], None)
        package = await app.prepare("newer-prepare", LEARNER)
        await ready(app, {"course_ref": ref(package["course_ref"])})
        assert (
            app.start("newer-start", LEARNER, ref(package["course_ref"]), materials_ready=True)[
                "decision"
            ]
            == "allow_simulation"
        )
        second = app.core
        deliver_all(second)
        await answer(app, "收到", "confirmation")
        await answer(app, second.course.tasks[second.task_index].acceptable_simulated[0])
        second.control("second-stop", "stop")
        app.seal()
        app.feedback("fb2", LEARNER, second.activity_id, "明确模拟：无额外补充")
        evaluation2 = await app.evaluate("eval2", LEARNER, second.activity_id, "final_feedback")
        await app.adopt("newer-adopt", LEARNER, evaluation2)
        newer = app.current(LEARNER, "state")
        assert newer.revision == reused.revision + 1
        old_finish = app.ledger.finish

        def fail_finish(operation_id, *args, **kwargs):
            if operation_id == operation:
                app.store.fail_at = "ledger"
            return old_finish(operation_id, *args, **kwargs)

        if lose_completion:
            app.ledger.finish = fail_finish
            with pytest.raises(OSError):
                await app.adopt("reused-state-adopt", LEARNER, evaluation1)
            app.ledger.finish = old_finish
            already_saved = app.current(LEARNER, "demand").ref
        result = await app.adopt("reused-state-adopt", LEARNER, evaluation1)
        assert app.current(LEARNER, "state").ref == newer.ref
        assert app.current(LEARNER, "state").source_refs == (evaluation2.ref,)
        assert result["state_ref"] == newer.ref.model_dump()
        demand = app.store.read(ref(result["demand_ref"]), LEARNER)
        assert demand.body["state_ref"] == newer.ref.model_dump()
        assert demand.revision == 5  # one more for the topic chosen before the newer lesson
        if lose_completion:
            assert demand.ref == already_saved
        steps = app.ledger.steps(operation, LEARNER)
        assert next(row["payload"] for row in steps if row["step_id"] == "state") == state_step
        assert any(
            row["step_id"].startswith("demand-recheck:")
            and row["payload"]["execution"] == "complete"
            and newer.ref.model_dump() in row["payload"]["input_refs"]
            for row in steps
        )
        calls = len(app.models.calls)
        assert await app.adopt("reused-state-adopt", LEARNER, evaluation1) == result
        assert app.current(LEARNER, "demand").ref == demand.ref
        assert len(app.models.calls) == calls

    asyncio.run(scenario())


def test_step_input_conflict_is_detected_before_business_publication(application):
    app = application
    old = app.current(LEARNER, "state")
    operation = app.request("preflight", LEARNER, "test-preflight", {})
    candidate = document(
        "state", old.object_id, operation, LEARNER, old.body, refs=(old.ref,), revision=2
    )
    publication = Publication(ref=candidate.ref, expected_revision=1, reason="明确模拟")
    app.store.fail_at = "before_commit"
    with pytest.raises(OSError):
        app.save_step(
            operation,
            LEARNER,
            "state",
            [candidate],
            publications=(publication,),
            baselines=(old.ref,),
        )
    with pytest.raises(Conflict, match="inputs cannot change"):
        app.save_step(operation, LEARNER, "state", [candidate], publications=(publication,))
    assert app.current(LEARNER, "state").ref == old.ref
    assert (
        app.store.receipt(
            SaveKey(operation_id=operation, step_id="state", source_revision=1), LEARNER
        )
        is None
    )
    with pytest.raises(LookupError):
        app.store.read(candidate.ref, LEARNER)
