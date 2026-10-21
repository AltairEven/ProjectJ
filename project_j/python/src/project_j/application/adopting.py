"""Coordinator use cases after an acknowledged evaluation: state, plan and the next demand,
then the student's goal decision and choice of topic and skills."""

import logging
from dataclasses import replace

from project_j.application.documents import identity, publication, successor
from project_j.application.ledger import Step
from project_j.application.progress import lesson_score_summary
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.corrections import release_rechecked
from project_j.learning.adoption import adopt_state
from project_j.learning.catalog import Capability, define
from project_j.learning.contracts import DemandBody, LearningState
from project_j.learning.maintenance import LEVELS
from project_j.learning.planning import (
    PlanningUnavailable,
    apply_demand_draft,
    demand_request,
    week_plan_after,
)
from project_j.platform.storage import (
    Conflict,
    canonical,
    digest,
)

log = logging.getLogger(__name__)


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
            self.models.require()
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
                    self.recent_titles(learner_id),
                    lesson_score_summary(self, learner_id, "planning"),
                )
            )
            activity_id = evaluation.body["activity_id"]
            if not result.ok:
                log.warning("planning activity=%s failure=%s", activity_id, result.failure)
                raise PlanningUnavailable(result.failure)
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
                        publication(new_plan, f"授权内周计划小调整：{result.output.plan_reason}")
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
                    publication(obj, "规划模型提出、规则核对后的下节需求"),
                    *publications,
                ),
                baselines=baselines,
            )
            log.info(
                "planning activity=%s goals=%s/%s action=%s plan_effect=%s topics=%d",
                activity_id,
                data.primary_goal.dimension_id,
                data.auxiliary_goal.dimension_id,
                result.output.suggestion_action,
                data.plan_effect,
                len(data.topic_suggestions),
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
            next_body = adopt_state(LearningState.model_validate(old.model_dump()), evaluation)
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
                        required_input=("无变化：沿用当前有效状态。",),
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
                    publications=(publication(obj, "只采用评价支持维度，保持未知"),),
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
                    publications=(publication(obj, "按更正后的状态复核需求，不重新规划"),),
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
            raise Conflict("这个目标已不是等你确认的版本。", demand.ref)
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
                            limitations=("规划提出、学生确认；定义不构成学习观察或掌握。",),
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
                        obj, "学生确认新目标" if choice == "accept" else "学生不同意换目标"
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
    ) -> dict:
        """The student's topic and ticks for the next lesson, a new demand revision; preparation
        waits for it."""
        signature = {
            "demand_ref": demand_ref.model_dump(),
            "topic": topic,
            "skills": list(skills),
            "note": note,
        }
        completed = self.ledger.completed_request(request_id, learner_id, "focus", signature)
        if completed:
            return completed
        demand = self.current(learner_id, "demand")
        if demand.ref != demand_ref or demand.body.get("topic"):
            raise Conflict("这一版需求已不需要选择，请刷新。", demand.ref)
        chosen = DemandBody.model_validate(
            demand.body
            | {
                "topic": topic.strip(),
                "skill_focus": list(skills),
                "skill_note": (note or "").strip() or None,
            }
        )
        operation = self.request(
            request_id,
            learner_id,
            "focus",
            signature,
            sources=(demand.ref,),
            baselines=(demand.ref,),
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
            saved = self.save_step(
                operation,
                learner_id,
                "demand",
                [obj],
                publications=(publication(obj, "学生选择本课题材与加强项目"),),
                baselines=(demand.ref,),
            )
        listed = chosen.topic in {item.title for item in chosen.topic_suggestions}
        log.info(
            "focus chosen topic=%s skills=%d note=%s",
            "suggested" if listed else "own",
            len(skills),
            chosen.skill_note is not None,
        )
        return self.ledger.finish(operation, learner_id, {"demand_ref": saved[0].ref.model_dump()})

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
        request = demand_request(
            evaluation.body,
            self.store.read(body.state_ref, learner_id).body,
            {level: self.current(learner_id, f"plan:{level}").body for level in LEVELS},
            self.store.read(body.profile_ref, learner_id).body,
            catalog,
            self.store.read(choice.suggestion_ref, learner_id).body,
            previous.model_dump(mode="json"),
            self.recent_titles(learner_id),
            lesson_score_summary(self, learner_id, "planning"),
        )
        self.models.require()
        result = await self.models.run(
            replace(request, context=request.context | {"keep_goals": keep})
        )
        if not result.ok:
            raise PlanningUnavailable(result.failure)
        draft = result.output
        if (draft.primary_goal.dimension_id, draft.auxiliary_goal.dimension_id) != (
            previous.primary_goal.dimension_id,
            previous.auxiliary_goal.dimension_id,
        ):
            raise PlanningUnavailable("没有沿用原目标")
        known = {item["dimension_id"] for item in catalog}
        kept = apply_demand_draft(
            draft,
            previous,
            known,
            body.state_ref,
            body.plan_ref,
            choice.suggestion_ref,
            plan_adjusted=False,
        )
        return kept.model_copy(update={"goal_confirmation": "confirmed"})
