"""Reviews and explicit decisions reuse existing evidence, never add observations."""

import logging
from datetime import UTC, datetime

from project_j.application.documents import document, identity
from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody, PlanBody
from project_j.learning.planning import (
    PlanningUnavailable,
    ReviewDraft,
    apply_review_draft,
    checked_plan,
    review_request,
)
from project_j.platform.storage import Conflict, Publication

log = logging.getLogger(__name__)
LEVELS = ("long_term", "annual", "stage", "week")


def catalog_ids(app, learner_id: str) -> set[str]:
    return {doc.body["dimension_id"] for doc in app.store.list_current(learner_id, "capability")}


def applicable_plan(app, learner_id: str, body: dict) -> PlanBody:
    return checked_plan(body, catalog_ids(app, learner_id))


async def review(
    app, request_id: str, learner_id: str, level: str, proposed_patch: dict | None = None
):
    completed = app.ledger.completed_request(
        request_id, learner_id, "review", {"level": level, "patch": proposed_patch}
    )
    if completed:
        return completed
    app.models.require()
    if level not in LEVELS:
        raise ValueError("unknown review level")
    plan = app.current(learner_id, f"plan:{level}")
    if proposed_patch:
        # A proposal that can never be applied is rejected before anyone can accept it.
        applicable_plan(app, learner_id, plan.body | proposed_patch)
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
        demand = app.current(learner_id, "demand")
        household = app.store.read(
            RevisionRef.model_validate(demand.body["household_ref"]), learner_id
        )
        catalog = [doc.body for doc in app.store.list_current(learner_id, "capability")]
        reply = await app.models.run(
            review_request(
                level,
                {lv: app.current(learner_id, f"plan:{lv}").body for lv in LEVELS},
                state.body,
                [e.body for e in evaluations],
                household.body,
                catalog,
            )
        )
        if not reply.ok:
            log.warning("review level=%s failure=%s", level, reply.failure)
            raise PlanningUnavailable(reply.failure, "复盘")
        draft = reply.output
        adjusted, pending, checks = apply_review_draft(
            level, draft, plan.body, {item["dimension_id"] for item in catalog}
        )
        if proposed_patch:  # The parent's own proposal replaces the model's patch and notes.
            adjusted, pending, checks = None, proposed_patch, []
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
                "conclusion": draft.conclusion,
                "unknown": list(draft.unknown),
                "plan_effect": "adjusted" if adjusted else "reused",
                "next_review_condition": draft.next_review_condition,
                "checks": checks,
                "pending_patch": pending,
                # A retry rebuilds the proposal from this draft instead of asking again.
                "draft": draft.model_dump(mode="json"),
            },
            refs=(plan.ref, state.ref),
            sources=tuple(e.ref for e in evaluations),
        )
        objects = [obj]
        publications = [Publication(ref=obj.ref, reason="复盘复用原证据，不新增活动")]
        if adjusted:
            new_plan = document(
                "plan",
                plan.object_id,
                operation,
                learner_id,
                adjusted,
                refs=plan.dependency_refs,
                sources=(obj.ref,),
                revision=plan.revision + 1,
            )
            objects.append(new_plan)
            publications.append(
                Publication(
                    ref=new_plan.ref,
                    expected_revision=plan.revision,
                    reason=f"授权内周计划小调整：{draft.reason}",
                )
            )
        saved = app.save_step(
            operation,
            learner_id,
            "review",
            objects,
            publications=tuple(publications),
            baselines=(plan.ref,) if adjusted else (),
        )
    body = saved[0].body
    draft = ReviewDraft.model_validate(body["draft"])
    result = {
        "review_ref": saved[0].ref.model_dump(),
        "plan_ref": saved[-1].ref.model_dump() if len(saved) > 1 else plan.ref.model_dump(),
        "plan_effect": body["plan_effect"],
    }
    if body["plan_effect"] == "adjusted":
        result["next_step"] = "recheck_demand_and_course"
    if patch := body.get("pending_patch"):
        decision = app.saved_step(operation, learner_id, "decision")
        if decision is None:
            parents = proposed_patch is not None
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
                    "patch": patch,
                    "reason": "重要计划变化须对具体版本决定。" if parents else draft.reason,
                    "impact": "保留既有有效安排，接受后仍需保存与后续需求/课程复核。"
                    if parents
                    else draft.impact,
                    "alternatives": ["沿用原安排", "取得更多依据后修改方案"]
                    if parents
                    else list(draft.alternatives) or ["沿用原安排"],
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
    log.info(
        "review level=%s plan_effect=%s decision=%s checks=%d",
        level,
        result["plan_effect"],
        "pending" if "decision_ref" in result else "none",
        len(body.get("checks", ())),
    )
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
        applicable_plan(app, learner_id, plan.body | proposal.body["patch"])
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
                app,
                learner_id,
                plan.body | proposal.body["patch"] | {"decision_ref": decided[0].ref.model_dump()},
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
