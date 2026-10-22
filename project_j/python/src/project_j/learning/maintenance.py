"""Reviews and explicit decisions reuse existing evidence, never add observations."""

import logging
from datetime import UTC, datetime

from project_j.application.documents import document, identity, publication, successor
from project_j.contracts import RevisionRef
from project_j.english import chinese
from project_j.learning.contracts import DemandBody, PlanBody
from project_j.learning.planning import (
    PlanningUnavailable,
    ReviewDraft,
    apply_review_draft,
    checked_plan,
    review_request,
)
from project_j.platform.storage import Conflict

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
        profile = app.store.read(RevisionRef.model_validate(demand.body["profile_ref"]), learner_id)
        catalog = [doc.body for doc in app.store.list_current(learner_id, "capability")]
        reply = await app.models.run(
            review_request(
                level,
                {lv: app.current(learner_id, f"plan:{lv}").body for lv in LEVELS},
                state.body,
                [e.body for e in evaluations],
                profile.body,
                catalog,
            )
        )
        if not reply.ok:
            log.warning("review level=%s failure=%s", level, reply.failure)
            raise PlanningUnavailable(reply.failure, "plan review")
        draft = reply.output
        if chinese(draft.model_dump()):
            log.warning("review level=%s failure=language", level)
            raise PlanningUnavailable("language", "plan review")
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
        publications = [
            publication(obj, "The review reuses the original evidence; no new activity")
        ]
        if adjusted:
            new_plan = successor(
                plan, operation, adjusted, refs=plan.dependency_refs, sources=(obj.ref,)
            )
            objects.append(new_plan)
            publications.append(
                publication(new_plan, f"Small weekly plan change within scope: {draft.reason}")
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
                    "reason": "A big plan change needs a decision on a given version."
                    if parents
                    else draft.reason,
                    "impact": "The current plan stays; after you accept, the next plan "
                    "and lesson are checked again."
                    if parents
                    else draft.impact,
                    "alternatives": ["Keep the current plan", "Change it after more evidence"]
                    if parents
                    else list(draft.alternatives) or ["Keep the current plan"],
                    "review_condition": "Confirm again if the plan or important conditions change.",
                },
                refs=(plan.ref, state.ref, saved[0].ref),
            )
            decision = app.save_step(
                operation,
                learner_id,
                "decision",
                [obj],
                publications=(
                    publication(obj, "The proposal waits for a decision; the plan stays as it is"),
                ),
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
        obj = successor(
            proposal,
            operation,
            proposal.body | {"decision_state": "accepted" if choice == "accept" else "rejected"},
            refs=proposal.dependency_refs,
            sources=(proposal.ref,),
        )
        decided = app.save_step(
            operation,
            learner_id,
            "decision",
            [obj],
            publications=(publication(obj, "The student's decision, bound to the exact versions"),),
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
            obj = successor(
                plan,
                operation,
                data,
                refs=plan.dependency_refs + (decided[0].ref,),
                sources=(proposal.ref,),
            )
            changed = app.save_step(
                operation,
                learner_id,
                "plan",
                [obj],
                publications=(
                    publication(
                        obj,
                        "Accepting is not taking effect; it takes effect once this step is saved",
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
        obj = successor(
            old, operation, body, refs=(state.ref, plan.ref, body.profile_ref), sources=(old.ref,)
        )
        saved = app.save_step(
            operation,
            learner_id,
            "demand",
            [obj],
            publications=(publication(obj, "Demand rechecked against the saved plan"),),
            baselines=(state.ref, plan.ref, old.ref),
        )
    return app.ledger.finish(operation, learner_id, {"demand_ref": saved[0].ref.model_dump()})
