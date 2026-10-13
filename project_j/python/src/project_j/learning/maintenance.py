"""Reviews and explicit decisions reuse existing evidence, never add observations."""

from datetime import UTC, datetime

from project_j.application.documents import document, identity
from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody, PlanBody
from project_j.platform.storage import Conflict, Publication


def applicable_plan(body: dict) -> PlanBody:
    data = PlanBody.model_validate(body)
    if data.budget_minutes != 30:
        raise ValueError(
            "the adopted MVP remains 30 minutes; "
            "a different baseline needs a separate design decision"
        )
    return data


def review(app, request_id: str, learner_id: str, level: str, proposed_patch: dict | None = None):
    completed = app.ledger.completed_request(
        request_id, learner_id, "review", {"level": level, "patch": proposed_patch}
    )
    if completed:
        return completed
    if level not in {"week", "stage", "annual", "long_term"}:
        raise ValueError("unknown review level")
    plan = app.current(learner_id, f"plan:{level}")
    if proposed_patch:
        # A proposal that can never be applied is rejected before anyone can accept it.
        applicable_plan(plan.body | proposed_patch)
    state = app.current(learner_id, "state")
    evaluations = app.store.list_current(learner_id, "evaluation")
    operation = app.request(
        request_id,
        learner_id,
        "review",
        {"level": level, "patch": proposed_patch},
        sources=tuple(e.ref for e in evaluations),
        baselines=(plan.ref, state.ref),
    )
    result = app.ledger.result(operation, learner_id)
    if result:
        return result
    saved = app.saved_step(operation, learner_id, "review")
    if saved is None:
        obj = document(
            "review",
            identity(operation, "review"),
            operation,
            learner_id,
            {
                "status": "checked",
                "level": level,
                "plan_baseline": plan.ref.model_dump(),
                "cutoff": datetime.now(UTC).isoformat(),
                "evidence_refs": [e.ref.model_dump() for e in evaluations],
                "conclusion": "只汇总已有范围；日期和课时不产生完成或掌握。",
                "unknown": ["保持、迁移及真实家庭负担仍未知。"],
                "plan_effect": "reused",
                "next_review_condition": "取得新日期、材料或重要家庭条件后再复核。",
            },
            refs=(plan.ref, state.ref),
            sources=tuple(e.ref for e in evaluations),
        )
        saved = app.save_step(
            operation,
            learner_id,
            "review",
            [obj],
            publications=(Publication(ref=obj.ref, reason="复盘复用原证据，不新增活动"),),
        )
    result = {
        "review_ref": saved[0].ref.model_dump(),
        "plan_ref": plan.ref.model_dump(),
        "plan_effect": "reused",
    }
    if proposed_patch:
        decision = app.saved_step(operation, learner_id, "decision")
        if decision is None:
            obj = document(
                "decision",
                identity(operation, "decision"),
                operation,
                learner_id,
                {
                    "status": "checked",
                    "decision_state": "pending",
                    "plan_ref": plan.ref.model_dump(),
                    "state_ref": state.ref.model_dump(),
                    "patch": proposed_patch,
                    "reason": "重要计划变化须对具体版本决定。",
                    "impact": "保留既有有效安排，接受后仍需保存与后续需求/课程复核。",
                    "alternatives": ["沿用原安排", "取得更多依据后修改方案"],
                    "review_condition": "基线或重要条件变化时待重新确认。",
                },
                refs=(plan.ref, state.ref, saved[0].ref),
            )
            decision = app.save_step(
                operation,
                learner_id,
                "decision",
                [obj],
                publications=(Publication(ref=obj.ref, reason="具体方案待决定，不改变原计划"),),
            )
        result["decision_ref"] = decision[0].ref.model_dump()
    return app.ledger.finish(operation, learner_id, result)


def decide(app, request_id: str, learner_id: str, decision_ref: RevisionRef, choice: str):
    if choice not in {"accept", "reject"}:
        raise ValueError("decision must be accept or reject for this exact proposal")
    proposal = app.store.read(decision_ref, learner_id)
    if proposal.kind != "decision" or proposal.body["decision_state"] != "pending":
        raise ValueError("proposal is not pending")
    operation = app.request(
        request_id,
        learner_id,
        "decision",
        {"decision_ref": decision_ref.model_dump(), "choice": choice},
        sources=(decision_ref,),
    )
    completed = app.ledger.result(operation, learner_id)
    if completed:
        return completed
    plan_ref = RevisionRef.model_validate(proposal.body["plan_ref"])
    state_ref = RevisionRef.model_validate(proposal.body["state_ref"])
    plan = app.store.current(plan_ref.object_id, learner_id)
    if choice == "accept" and (
        plan.ref != plan_ref or app.current(learner_id, "state").ref != state_ref
    ):
        raise Conflict("proposal baseline changed; a new concrete proposal must be confirmed")
    if choice == "accept":
        applicable_plan(plan.body | proposal.body["patch"])
    decided = app.saved_step(operation, learner_id, "decision")
    if decided is None:
        obj = document(
            "decision",
            proposal.object_id,
            operation,
            learner_id,
            proposal.body | {"decision_state": "accepted" if choice == "accept" else "rejected"},
            refs=proposal.dependency_refs,
            sources=(proposal.ref,),
            revision=proposal.revision + 1,
        )
        decided = app.save_step(
            operation,
            learner_id,
            "decision",
            [obj],
            publications=(
                Publication(
                    ref=obj.ref,
                    expected_revision=proposal.revision,
                    reason="保存绑定版本的模拟家长决定",
                ),
            ),
        )
    result = {
        "decision_ref": decided[0].ref.model_dump(),
        "choice": choice,
        "effect": "not_changed",
    }
    if choice == "accept":
        changed = app.saved_step(operation, learner_id, "plan")
        if changed is None:
            data = applicable_plan(
                plan.body | proposal.body["patch"] | {"decision_ref": decided[0].ref.model_dump()}
            )
            obj = document(
                "plan",
                plan.object_id,
                operation,
                learner_id,
                data,
                refs=plan.dependency_refs + (decided[0].ref,),
                sources=(proposal.ref,),
                revision=plan.revision + 1,
            )
            changed = app.save_step(
                operation,
                learner_id,
                "plan",
                [obj],
                publications=(
                    Publication(
                        ref=obj.ref,
                        expected_revision=plan.revision,
                        reason="接受不等于生效；此步保存后才生效",
                    ),
                ),
                baselines=(plan_ref, state_ref),
            )
        result.update(
            plan_ref=changed[0].ref.model_dump(),
            effect="plan_effective",
            next_step="recheck_demand_and_course",
        )
    return app.ledger.finish(operation, learner_id, result)


def revise_demand_after_plan(app, request_id: str, learner_id: str):
    completed = app.ledger.completed_request(request_id, learner_id, "demand-recheck", {})
    if completed:
        return completed
    state = app.current(learner_id, "state")
    plan = app.current(learner_id, "plan:week")
    old = app.current(learner_id, "demand")
    operation = app.request(
        request_id,
        learner_id,
        "demand-recheck",
        {"demand_ref": old.ref.model_dump(), "plan_ref": plan.ref.model_dump()},
        baselines=(state.ref, plan.ref, old.ref),
    )
    existing = app.ledger.result(operation, learner_id)
    if existing:
        return existing
    saved = app.saved_step(operation, learner_id, "demand")
    if saved is None:
        body = DemandBody.model_validate(
            old.body
            | {
                "state_ref": state.ref.model_dump(),
                "plan_ref": plan.ref.model_dump(),
                "primary_goal": plan.body["main_goal"],
            }
        )
        obj = document(
            "demand",
            old.object_id,
            operation,
            learner_id,
            body,
            refs=(state.ref, plan.ref, body.household_ref),
            sources=(old.ref,),
            revision=old.revision + 1,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "demand",
            [obj],
            publications=(
                Publication(
                    ref=obj.ref, expected_revision=old.revision, reason="以已保存计划复核需求"
                ),
            ),
            baselines=(state.ref, plan.ref, old.ref),
        )
    return app.ledger.finish(operation, learner_id, {"demand_ref": saved[0].ref.model_dump()})
