"""Coordinator use cases after an acknowledged evaluation: state, plan and the next demand,
then the student's goal decision and choice of topic and skills."""

import logging
from dataclasses import replace

from project_j.application.documents import identity, publication, successor
from project_j.application.ledger import Step
from project_j.application.progress import evaluation_job, lesson_score_summary
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.corrections import release_rechecked
from project_j.english import chinese
from project_j.learning.adoption import adopt_state
from project_j.learning.catalog import Capability, define
from project_j.learning.contracts import (
    Challenge,
    DemandBody,
    ProfileBody,
    challenge_of,
    recent_forms,
)
from project_j.learning.maintenance import LEVELS
from project_j.learning.onboarding import revised
from project_j.learning.planning import (
    PlanningUnavailable,
    apply_demand_draft,
    demand_request,
    difficulty_step,
    fixed_wording,
    week_plan_after,
)
from project_j.platform.storage import (
    Conflict,
    canonical,
    digest,
)

log = logging.getLogger(__name__)


def difficulty(evaluation: Document, course: dict) -> str | None:
    """The rule's step for planning the next lesson; the log keeps the kind only (ADR-0030)."""
    step, reason = difficulty_step(evaluation.body, course)
    activity = evaluation.body["activity_id"]
    log.info("planning activity=%s difficulty=%s reason=%s", activity, step or "none", reason)
    return step


class Adopting:
    async def adopt(self, request_id: str, learner_id: str, evaluation: Document):
        completed = self.ledger.completed_request(
            request_id, learner_id, "adopt", {"evaluation_ref": evaluation.ref.model_dump()}
        )
        if completed:
            return completed
        current_revision, _ = self.source_revision(evaluation.body["activity_id"], learner_id)
        if evaluation.source_revision != current_revision:
            raise Conflict("old evaluation source cannot publish a new learning baseline")
        operation = self.request(
            request_id,
            learner_id,
            "adopt",
            {"evaluation_ref": evaluation.ref.model_dump()},
            sources=(evaluation.ref,),
            source_revision=current_revision,
        )
        result = self.ledger.result(operation, learner_id)
        if result:
            return result
        state = self.adopted_state(operation, learner_id, evaluation, current_revision)
        plan = self.current(learner_id, "plan:week")
        suggestion = self.store.current(
            identity(evaluation.body["activity_id"], "suggestion"), learner_id
        )
        if suggestion is None or suggestion.body["evaluation_ref"] != evaluation.ref.model_dump():
            raise LookupError("saved candidate suggestion of this evaluation is missing")
        demand = self.saved_step(operation, learner_id, "demand", current_revision)
        if demand is None:
            for row in self.ledger.steps(operation, learner_id):
                if (
                    row["step_id"].startswith("demand-recheck:")
                    and row["source_revision"] == current_revision
                ):
                    demand = self.saved_step(
                        operation, learner_id, row["step_id"], current_revision
                    )
                    if demand is not None:
                        break
        if demand is None:
            old = self.current(learner_id, "demand")
            profile = self.current(learner_id, "profile")  # Its latest version, edits included.
            current = DemandBody.model_validate(old.body).model_copy(
                update={"profile_ref": profile.ref}
            )
            catalog = [doc.body for doc in self.store.list_current(learner_id, "capability")]
            known = {item["dimension_id"] for item in catalog}
            course = self.lesson_course(learner_id, evaluation)
            step = difficulty(evaluation, course)
            self.models.require()
            stories = self.recent_stories(learner_id)
            result = await self.models.run(
                demand_request(
                    evaluation.body,
                    state.body,
                    {
                        level: self.current(learner_id, f"plan:{level}").body
                        for level in ("long_term", "annual", "stage", "week")
                    },
                    profile.body,
                    catalog,
                    suggestion.body,
                    old.body,
                    [item["title"] for item in stories],
                    lesson_score_summary(self, learner_id, "planning"),
                    course=course,
                    recent_forms=recent_forms(stories),
                )
            )
            activity_id = evaluation.body["activity_id"]
            if self.source_revision(activity_id, learner_id)[0] != current_revision:
                # A report arrived while planning: this plan is from the old source and is never
                # published; the new evaluation plans again (design/shared/execution-recovery.md).
                log.info("planning activity=%s superseded", activity_id)
                return self.ledger.finish(operation, learner_id, {"superseded": True})
            if not result.ok:
                log.warning("planning activity=%s failure=%s", activity_id, result.failure)
                raise PlanningUnavailable(result.failure)
            if chinese(result.output.model_dump()):
                log.warning("planning activity=%s failure=language", activity_id)
                raise PlanningUnavailable("language")
            week_before = plan
            try:
                adjusted = week_plan_after(result.output, plan.body, known)
                objects, publications = [], []
                if adjusted is not None:
                    new_plan = successor(
                        plan,
                        operation,
                        adjusted,
                        source_revision=current_revision,
                        refs=plan.dependency_refs,
                        sources=(evaluation.ref,),
                        occurred_at=evaluation.occurred_at,
                    )
                    objects.append(new_plan)
                    publications.append(
                        publication(
                            new_plan,
                            f"Small weekly plan change within scope: {result.output.plan_reason}",
                        )
                    )
                    plan = new_plan
                data = apply_demand_draft(
                    result.output,
                    current,
                    known,
                    state.ref,
                    plan.ref,
                    suggestion.ref,
                    plan_adjusted=adjusted is not None,
                    step=step,
                    recent_forms=recent_forms(stories),
                )
            except PlanningUnavailable as error:
                log.warning("planning activity=%s rejected: %s", activity_id, error)
                raise
            baselines = (old.ref, state.ref, week_before.ref, data.profile_ref)
            input_digest = digest(canonical([ref.model_dump() for ref in baselines]))
            previous = [
                row["payload"]
                for row in self.ledger.steps(operation, learner_id)
                if row["step_id"] == "demand" and row["source_revision"] == current_revision
            ]
            # A changed baseline is a new recheck step, not a rewrite of the failed inputs.
            step_id = (
                f"demand-recheck:{input_digest}"
                if previous and previous[0]["input_digest"] != input_digest
                else "demand"
            )
            obj = successor(
                old,
                operation,
                data,
                source_revision=current_revision,
                refs=(state.ref, plan.ref, data.profile_ref, suggestion.ref),
                sources=(evaluation.ref,),
                occurred_at=evaluation.occurred_at,
            )
            # The plan revision and the demand that cites it are saved as one step.
            demand = self.save_step(
                operation,
                learner_id,
                step_id,
                [obj, *objects],
                source_revision=current_revision,
                publications=(
                    publication(
                        obj, "Next demand proposed by the planning model, checked by the rules"
                    ),
                    *publications,
                ),
                baselines=baselines,
            )
            log.info(
                "planning activity=%s goals=%s/%s direction=%s action=%s plan_effect=%s topics=%d "
                "topic_forms=%d topics_dropped=%d fixed_wording=%d",
                activity_id,
                data.primary_goal.dimension_id,
                data.auxiliary_goal.dimension_id,
                data.direction,
                result.output.suggestion_action,
                data.plan_effect,
                len(data.topic_suggestions),
                sum(item.form is not None for item in data.topic_suggestions),
                len(result.output.topic_suggestions) - len(data.topic_suggestions),
                fixed_wording(data),
            )
        body = demand[0].body
        release_rechecked(self, operation, learner_id, evaluation)
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "state_ref": state.ref.model_dump(),
                "plan_ref": body["plan_ref"],
                "plan_effect": body.get("plan_effect") or "reused",
                "plan_reason": body.get("plan_reason"),
                "demand_ref": demand[0].ref.model_dump(),
                "suggestion_ref": suggestion.ref.model_dump(),
            },
        )

    def adopted_state(
        self, operation: str, learner_id: str, evaluation: Document, current_revision: int
    ) -> Document:
        """The current state with this evaluation taken in, or the same one when nothing changed."""
        state = self.saved_step(operation, learner_id, "state", current_revision)
        if state is None:
            old = self.current(learner_id, "state")
            next_body = adopt_state(old, evaluation)
            if canonical(next_body) == canonical(old.body):
                state = [old]
                self.ledger.checkpoint(
                    operation,
                    learner_id,
                    Step(
                        step_id="state",
                        source_revision=current_revision,
                        owner="learning",
                        input_digest=digest(canonical([old.ref.model_dump()])),
                        input_refs=(old.ref,),
                        attempts=1,
                        execution="complete",
                        effect_refs=(old.ref,),
                        required_input=("No change: the current state stays in effect.",),
                    ),
                    replace_unfinished=True,
                )
            else:
                obj = successor(
                    old,
                    operation,
                    next_body,
                    source_revision=current_revision,
                    refs=(old.ref,),
                    sources=(evaluation.ref,),
                    occurred_at=evaluation.occurred_at,
                )
                state = self.save_step(
                    operation,
                    learner_id,
                    "state",
                    [obj],
                    source_revision=current_revision,
                    publications=(
                        publication(
                            obj,
                            "Only the dimensions the evaluation supports; the rest stay unknown",
                        ),
                    ),
                    baselines=(old.ref,),
                )
        state = state[0]
        if self.current(learner_id, "state").ref != state.ref:
            # Later evidence survives; retry continues against the currently valid state.
            state = self.current(learner_id, "state")
        return state

    async def recheck(self, request_id: str, learner_id: str, evaluation: Document) -> dict:
        """An older lesson's correction, acknowledged: the current state takes the corrected
        evaluation in (newer evidence stays) and the demand follows a changed state. The next
        lesson is not planned again from an older lesson."""
        signature = {"evaluation_ref": evaluation.ref.model_dump()}
        completed = self.ledger.completed_request(request_id, learner_id, "recheck", signature)
        if completed:
            return completed
        current_revision, _ = self.source_revision(evaluation.body["activity_id"], learner_id)
        if evaluation.source_revision != current_revision:
            raise Conflict("old evaluation source cannot recheck the learning baseline")
        operation = self.request(
            request_id,
            learner_id,
            "recheck",
            signature,
            sources=(evaluation.ref,),
            source_revision=current_revision,
        )
        result = self.ledger.result(operation, learner_id)
        if result:
            return result
        state = self.adopted_state(operation, learner_id, evaluation, current_revision)
        demand = self.current(learner_id, "demand")
        follows = demand.body["state_ref"] != state.ref.model_dump()
        if follows:
            saved = self.saved_step(operation, learner_id, "demand", current_revision)
            if saved is None:
                body = DemandBody.model_validate(
                    demand.body | {"state_ref": state.ref.model_dump()}
                )
                obj = successor(
                    demand,
                    operation,
                    body,
                    source_revision=current_revision,
                    refs=(state.ref,)
                    + tuple(r for r in demand.dependency_refs if r.object_id != state.object_id),
                    # Keeps the latest lesson's evaluation: it still planned this demand.
                    sources=(*demand.source_refs, evaluation.ref),
                )
                saved = self.save_step(
                    operation,
                    learner_id,
                    "demand",
                    [obj],
                    source_revision=current_revision,
                    publications=(
                        publication(
                            obj, "Demand rechecked against the corrected state; no replanning"
                        ),
                    ),
                    baselines=(demand.ref, state.ref),
                )
            demand = saved[0]
        log.info(
            "learning recheck activity=%s state_revision=%d demand=%s",
            evaluation.body["activity_id"],
            state.revision,
            "rechecked" if follows else "kept",
        )
        release_rechecked(self, operation, learner_id, evaluation)
        return self.ledger.finish(
            operation,
            learner_id,
            {"state_ref": state.ref.model_dump(), "demand_ref": demand.ref.model_dump()},
        )

    async def decide_goal(
        self, request_id: str, learner_id: str, demand_ref: RevisionRef, choice: str
    ) -> dict:
        """The student accepts a changed goal (and its new capabilities) or keeps the old one."""
        signature = {"demand_ref": demand_ref.model_dump(), "choice": choice}
        completed = self.ledger.completed_request(request_id, learner_id, "goal", signature)
        if completed:
            return completed
        if choice not in {"accept", "reject"}:
            raise ValueError("choice must be accept or reject")
        demand = self.current(learner_id, "demand")
        if demand.ref != demand_ref or demand.body.get("goal_confirmation") != "needed":
            raise Conflict("This goal is no longer the version waiting for you.", demand.ref)
        body = DemandBody.model_validate(demand.body)
        operation = self.request(
            request_id,
            learner_id,
            "goal",
            signature,
            sources=(demand.ref,),
            baselines=(demand.ref,),
        )
        result = self.ledger.result(operation, learner_id)
        if result:
            return result
        saved = self.saved_step(operation, learner_id, "demand")
        if saved is None:
            if choice == "accept":
                for item in body.proposed_capabilities:
                    define(
                        self,
                        f"{request_id}:capability:{item.dimension_id}",
                        learner_id,
                        Capability(
                            dimension_id=item.dimension_id,
                            subject=item.dimension_id.rsplit(".", 1)[0],
                            name=item.name,
                            definition=item.definition,
                            limitations=(
                                "Planned and confirmed; a definition is not an observation.",
                            ),
                        ),
                    )
                decided = body.model_copy(update={"goal_confirmation": "confirmed"})
            else:
                decided = await self.replan_keeping_goals(learner_id, demand, body)
            obj = successor(
                demand,
                operation,
                decided,
                refs=demand.dependency_refs,
                sources=demand.source_refs,
                occurred_at=demand.occurred_at,
            )
            saved = self.save_step(
                operation,
                learner_id,
                "demand",
                [obj],
                publications=(
                    publication(
                        obj,
                        "The student accepted the new goal"
                        if choice == "accept"
                        else "The student kept the old goal",
                    ),
                ),
                baselines=(demand.ref,),
            )
        log.info(
            "goal decided choice=%s new=%d",
            choice,
            len(body.proposed_capabilities) if choice == "accept" else 0,
        )
        return self.ledger.finish(
            operation, learner_id, {"demand_ref": saved[0].ref.model_dump(), "choice": choice}
        )

    def choose_focus(
        self,
        request_id: str,
        learner_id: str,
        demand_ref: RevisionRef,
        topic: str,
        skills: list[str],
        note: str | None,
        challenge: Challenge | None = None,
    ) -> dict:
        """The student's topic and ticks for the next lesson, a new demand revision; preparation
        waits for it. A changed story step is a new profile revision in the same step."""
        signature = {
            "demand_ref": demand_ref.model_dump(),
            "topic": topic,
            "skills": list(skills),
            "note": note,
            "challenge": challenge,
        }
        completed = self.ledger.completed_request(request_id, learner_id, "focus", signature)
        if completed:
            return completed
        demand = self.current(learner_id, "demand")
        if demand.ref != demand_ref or demand.body.get("topic"):
            raise Conflict("This plan no longer needs a choice. Please refresh.", demand.ref)
        if evaluation_job(self, learner_id) is not None:
            # A newer evaluation plans this demand again first; its topic would be dropped.
            log.info("focus refused reason=planning")
            raise Conflict("Jane is still planning the next lesson. Please wait.", demand.ref)
        # A listed topic brings its form; the student's own topic leaves it to preparation.
        form = next(
            (
                item.get("form")
                for item in demand.body.get("topic_suggestions", [])
                if item["title"] == topic.strip()
            ),
            None,
        )
        chosen = DemandBody.model_validate(
            demand.body
            | {
                "topic": topic.strip(),
                "form": form,
                "skill_focus": list(skills),
                "skill_note": (note or "").strip() or None,
            }
        )
        profile = self.current(learner_id, "profile")
        changed = challenge is not None and challenge != challenge_of(profile.body)
        baselines = (demand.ref, profile.ref) if changed else (demand.ref,)
        operation = self.request(
            request_id,
            learner_id,
            "focus",
            signature,
            sources=(demand.ref,),
            baselines=baselines,
        )
        result = self.ledger.result(operation, learner_id)
        if result:
            return result
        saved = self.saved_step(operation, learner_id, "demand")
        if saved is None:
            obj = successor(
                demand,
                operation,
                chosen,
                refs=demand.dependency_refs,
                sources=demand.source_refs,
                occurred_at=demand.occurred_at,
            )
            objects = [obj]
            publications = [
                publication(obj, "The student chose the topic and skills for this lesson")
            ]
            if changed:
                body = ProfileBody.model_validate(profile.body)
                step = revised(
                    operation, learner_id, profile, body.model_copy(update={"challenge": challenge})
                )
                objects.append(step)
                publications.append(publication(step, "The student chose how hard stories are"))
            saved = self.save_step(
                operation,
                learner_id,
                "demand",
                objects,
                publications=tuple(publications),
                baselines=baselines,
            )
        listed = chosen.topic in {item.title for item in chosen.topic_suggestions}
        log.info(
            "focus chosen topic=%s form=%s skills=%d note=%s challenge=%s changed=%s",
            "suggested" if listed else "own",
            chosen.form or "none",
            len(skills),
            chosen.skill_note is not None,
            challenge or "kept",
            "true" if changed else "false",
        )
        return self.ledger.finish(operation, learner_id, {"demand_ref": saved[0].ref.model_dump()})

    def lesson_course(self, learner_id: str, evaluation: Document) -> dict:
        """The course the evaluated lesson taught."""
        ref = RevisionRef.model_validate(evaluation.body["alignment"]["tasks_materials"])
        return self.store.read(ref, learner_id).body

    async def replan_keeping_goals(self, learner_id: str, demand: Document, body: DemandBody):
        """Planning again from the same evaluation, told to keep the previous two goals."""
        previous = DemandBody.model_validate(
            self.store.read(
                RevisionRef(object_id=demand.object_id, revision=demand.revision - 1), learner_id
            ).body
        )
        evaluation = next(
            self.store.read(ref, learner_id)
            for ref in demand.source_refs
            if self.store.read(ref, learner_id).kind == "evaluation"
        )
        choice = body.suggestion_choices[0]
        catalog = [doc.body for doc in self.store.list_current(learner_id, "capability")]
        keep = {
            "primary_goal": previous.primary_goal.model_dump(mode="json"),
            "auxiliary_goal": previous.auxiliary_goal.model_dump(mode="json"),
        }
        course = self.lesson_course(learner_id, evaluation)
        step = difficulty(evaluation, course)
        stories = self.recent_stories(learner_id)
        request = demand_request(
            evaluation.body,
            self.store.read(body.state_ref, learner_id).body,
            {level: self.current(learner_id, f"plan:{level}").body for level in LEVELS},
            self.store.read(body.profile_ref, learner_id).body,
            catalog,
            self.store.read(choice.suggestion_ref, learner_id).body,
            previous.model_dump(mode="json"),
            [item["title"] for item in stories],
            lesson_score_summary(self, learner_id, "planning"),
            course=course,
            recent_forms=recent_forms(stories),
        )
        self.models.require()
        result = await self.models.run(
            replace(request, context=request.context | {"keep_goals": keep})
        )
        if not result.ok:
            raise PlanningUnavailable(result.failure)
        draft = result.output
        if chinese(draft.model_dump()):
            log.warning("planning activity=%s failure=language", evaluation.body["activity_id"])
            raise PlanningUnavailable("language")
        if (draft.primary_goal.dimension_id, draft.auxiliary_goal.dimension_id) != (
            previous.primary_goal.dimension_id,
            previous.auxiliary_goal.dimension_id,
        ):
            raise PlanningUnavailable("the goals were not kept")
        known = {item["dimension_id"] for item in catalog}
        try:
            kept = apply_demand_draft(
                draft,
                previous,
                known,
                body.state_ref,
                body.plan_ref,
                choice.suggestion_ref,
                plan_adjusted=False,
                step=step,
                recent_forms=recent_forms(stories),
            )
        except PlanningUnavailable as error:
            log.warning("planning activity=%s rejected: %s", evaluation.body["activity_id"], error)
            raise
        return kept.model_copy(update={"goal_confirmation": "confirmed"})
