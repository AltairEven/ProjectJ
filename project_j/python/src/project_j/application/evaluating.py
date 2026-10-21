"""Coordinator use cases of evaluation: feedback, lost reports, acknowledgement and the
evaluation of a lesson."""

import asyncio
import logging
from datetime import UTC, datetime

from project_j.application.documents import document, identity, publication
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.corrections import affected
from project_j.evaluation.assessment import (
    DraftMismatch,
    EvaluationDraft,
    EvaluationUnavailable,
    apply_draft,
    evaluation_request,
    interpret,
    next_suggestion,
)
from project_j.platform.storage import (
    Conflict,
)

log = logging.getLogger(__name__)


class Evaluating:
    def activity_facts(self, activity_id: str, learner_id: str):
        fact = self.store.current(identity(activity_id, "fact"), learner_id)
        if fact is None:
            raise LookupError("facts have not been sealed; do not reconstruct from the course")
        return fact

    def feedbacks(self, activity_id: str, learner_id: str):
        return sorted(
            [
                doc
                for doc in self.store.list_current(learner_id, "feedback")
                if doc.body["activity_id"] == activity_id
            ],
            key=lambda doc: doc.source_revision,
        )

    def source_revision(self, activity_id: str, learner_id: str):
        feedbacks = self.feedbacks(activity_id, learner_id)
        return self.activity_facts(activity_id, learner_id).revision + len(feedbacks), feedbacks

    def feedback(
        self, request_id: str, learner_id: str, activity_id: str, text: str, corrections=()
    ):
        completed = self.ledger.completed_request(
            request_id,
            learner_id,
            "feedback",
            {"activity_id": activity_id, "text": text, "corrections": list(corrections)},
        )
        if completed:
            return completed
        fact = self.activity_facts(activity_id, learner_id)
        operation = self.request(
            request_id,
            learner_id,
            "feedback",
            {"activity_id": activity_id, "text": text, "corrections": list(corrections)},
            sources=(fact.ref,),
        )
        existing = self.ledger.result(operation, learner_id)
        if existing:
            return existing
        saved = self.saved_step(operation, learner_id, "feedback", None)
        if saved is None:
            source_revision, old = self.source_revision(activity_id, learner_id)
            obj = document(
                "feedback",
                identity(operation, "feedback"),
                operation,
                learner_id,
                {
                    "status": "checked",
                    "activity_id": activity_id,
                    "fact_ref": fact.ref.model_dump(),
                    "text": text,
                    "provider": "student" if self.store.realm == "study" else "simulated",
                    "occurred_at": None,
                    "reported_at": datetime.now(UTC).isoformat(),
                    "corrections": list(corrections),
                    "limitations": ["无额外补充不等于独立。"],
                },
                refs=(fact.ref,),
                sources=tuple(fb.ref for fb in old),
                source_revision=source_revision + 1,
            )
            saved = self.save_step(
                operation,
                learner_id,
                "feedback",
                [obj],
                source_revision=obj.source_revision,
                publications=(publication(obj, "追加原活动来源，不制造新活动或尝试"),),
            )
        log.info(
            "feedback saved activity=%s objection=%s",
            activity_id,
            "true" if corrections else "false",
        )
        evaluation = self.store.current(identity(activity_id, "evaluation"), learner_id)
        if corrections and evaluation:
            # Derived state/demand/course stay limited until the owners recheck the new source.
            for doc in [evaluation, *affected(self, evaluation.ref, learner_id)]:
                if self.uses_corrected_source(learner_id, doc, activity_id):
                    self.restrict(
                        operation, learner_id, doc, "新更正影响旧解释，待同活动来源复核。"
                    )
        return self.ledger.finish(
            operation,
            learner_id,
            {"feedback_ref": saved[0].ref.model_dump(), "evaluation": "not_requested"},
        )

    def lost_report(self, request_id: str, learner_id: str, activity_id: str, text: str):
        """A parent's later report on a class lost with process memory is a new actual source;
        it is only saved, never replayed into a fact or sent to evaluation."""
        body = {"activity_id": activity_id, "text": text}
        completed = self.ledger.completed_request(request_id, learner_id, "lost-report", body)
        if completed:
            return completed
        lost = next(
            (
                item
                for item in self.lost_activities(learner_id, self.store.list_current(learner_id))
                if item["activity_id"] == activity_id
            ),
            None,
        )
        if lost is None:
            raise LookupError("not a lost class of this learner; check the activity identity")
        start_ref = RevisionRef.model_validate(lost["start_ref"])
        operation = self.request(request_id, learner_id, "lost-report", body, sources=(start_ref,))
        existing = self.ledger.result(operation, learner_id)
        if existing:
            return existing
        report = self.saved_step(operation, learner_id, "parent-report")
        if report is None:
            obj = document(
                "parent_report",
                identity(operation, "parent-report"),
                operation,
                learner_id,
                {
                    "status": "checked",
                    "activity_id": activity_id,
                    "requested_at": lost["requested_at"],
                    "course_ref": lost["course_ref"],
                    "text": text,
                    "provider": "student" if self.store.realm == "study" else "simulated",
                    "reported_at": datetime.now(UTC).isoformat(),
                    "gap": lost["state"],
                    "limitations": [
                        "学生的补报是新的实际来源；不补造原话、不重复计样本，不自动进入评价。"
                    ],
                },
                refs=(start_ref,),
            )
            report = self.save_step(
                operation,
                learner_id,
                "parent-report",
                [obj],
                publications=(publication(obj, "只保存学生补报，缺口保留"),),
            )
        return self.ledger.finish(operation, learner_id, {"report_ref": report[0].ref.model_dump()})

    def uses_corrected_source(self, learner_id: str, doc: Document, activity_id: str) -> bool:
        """Only this activity's interpretation and current or untaught derived objects use it;
        later lessons' own evidence and lessons already taught stay valid history."""
        if doc.kind in {"evaluation", "report"}:
            return doc.body.get("activity_id") == activity_id
        if doc.kind == "suggestion":
            return doc.body["evaluation_ref"]["object_id"] == identity(activity_id, "evaluation")
        if doc.kind in {"state", "demand", "plan", "profile"}:
            return self.store.current(doc.object_id, learner_id).ref == doc.ref
        if doc.kind == "start":
            return False
        taught = {
            fact.body["course_ref"]["object_id"]
            for fact in self.store.list_current(learner_id, "fact")
        }
        package = {doc.object_id} | {ref.object_id for ref in doc.dependency_refs}
        return not package & taught

    def acknowledged(self, learner_id: str, activity_id: str) -> Document | None:
        """The acknowledgement of the activity's current evaluation, if the student gave one."""
        evaluation = self.store.current(identity(activity_id, "evaluation"), learner_id)
        mark = self.store.current(identity(activity_id, "acknowledgement"), learner_id)
        if evaluation and mark and mark.body["acknowledged_ref"] == evaluation.ref.model_dump():
            return mark
        return None

    def acknowledge(
        self, request_id: str, learner_id: str, activity_id: str, evaluation_ref: RevisionRef
    ) -> dict:
        """The student accepts one exact evaluation version; adoption may then go on."""
        signature = {"activity_id": activity_id, "evaluation_ref": evaluation_ref.model_dump()}
        completed = self.ledger.completed_request(request_id, learner_id, "acknowledge", signature)
        if completed:
            return completed
        current = self.store.current(identity(activity_id, "evaluation"), learner_id)
        if current is None or current.ref != evaluation_ref:
            raise Conflict("评价已更新，请查看新版本后再认可。", current.ref if current else None)
        if current.source_revision != self.source_revision(activity_id, learner_id)[0]:
            raise Conflict("评价已更新，请查看新版本后再认可。")
        operation = self.request(
            request_id, learner_id, "acknowledge", signature, sources=(current.ref,)
        )
        saved = self.saved_step(operation, learner_id, "acknowledgement")
        if saved is None:
            prior = self.store.current(identity(activity_id, "acknowledgement"), learner_id)
            obj = document(
                "report",
                identity(activity_id, "acknowledgement"),
                operation,
                learner_id,
                {
                    "status": "checked",
                    "activity_id": activity_id,
                    "acknowledged_ref": current.ref.model_dump(),
                },
                refs=(current.ref,),
                revision=prior.revision + 1 if prior else 1,
            )
            saved = self.save_step(
                operation,
                learner_id,
                "acknowledgement",
                [obj],
                publications=(publication(obj, "学生认可这一评价版本"),),
            )
        log.info("evaluation acknowledged activity=%s revision=%d", activity_id, current.revision)
        return self.ledger.finish(
            operation,
            learner_id,
            {"acknowledged_ref": current.ref.model_dump(), "ack_ref": saved[0].ref.model_dump()},
        )

    async def evaluate(self, request_id: str, learner_id: str, activity_id: str, purpose: str):
        self.models.require()
        fact = self.activity_facts(activity_id, learner_id)
        source_revision, feedbacks = self.source_revision(activity_id, learner_id)
        current = self.store.current(identity(activity_id, "evaluation"), learner_id)
        if current and current.source_revision == source_revision:
            return current
        for (owner, activity, _), active in tuple(self.evaluation_jobs.items()):
            if (
                owner == learner_id
                and activity == activity_id
                and active is not asyncio.current_task()
            ):
                return await asyncio.shield(active)
        if not feedbacks and purpose != "after_class":
            # The after-class job evaluates once on its own; a refresh never does.
            raise ValueError("only the after-class evaluation runs without a note")
        job_key = (learner_id, activity_id, source_revision)
        if job_key in self.evaluation_jobs:
            return await asyncio.shield(self.evaluation_jobs[job_key])

        async def generate():
            operation = self.request(
                request_id,
                learner_id,
                "evaluate",
                {"activity_id": activity_id, "purpose": purpose},
                sources=(fact.ref,) + tuple(fb.ref for fb in feedbacks),
                source_revision=source_revision,
            )
            saved = self.saved_step(operation, learner_id, "evaluation", source_revision)
            if saved:
                return saved[0]
            prior = self.store.current(identity(activity_id, "evaluation"), learner_id)
            same_fact = prior is not None and prior.body["fact_ref"] == fact.ref.model_dump()
            no_new_facts = (
                bool(feedbacks)
                and same_fact
                and all(
                    fb.body["text"].strip() == "无额外补充" and not fb.body["corrections"]
                    for fb in feedbacks
                )
            )
            # No new facts: reuse the checked draft instead of asking the model again.
            draft = (
                EvaluationDraft.model_validate(prior.body["draft"])
                if no_new_facts and prior.body.get("draft")
                else None
            )
            if draft is None:
                course = self.store.read(
                    RevisionRef.model_validate(fact.body["course_ref"]), learner_id
                )
                jane = self.store.read(
                    RevisionRef.model_validate(course.body["jane_ref"]), learner_id
                )
                result = await self.models.run(
                    evaluation_request(
                        fact.body, course.body, [fb.body for fb in feedbacks], jane.body
                    )
                )
                if not result.ok:
                    log.warning("evaluation activity=%s failure=%s", activity_id, result.failure)
                    raise EvaluationUnavailable(result.failure)
                draft = result.output
            else:
                log.info("evaluation activity=%s reused the checked draft", activity_id)
            current_revision, current_feedback = self.source_revision(activity_id, learner_id)
            if current_revision != source_revision:
                # Re-enter with the new source, never publish the late old interpretation.
                return await self.evaluate(
                    f"{request_id}:source:{current_revision}", learner_id, activity_id, purpose
                )
            prior = self.store.current(identity(activity_id, "evaluation"), learner_id)
            prior_suggestion = self.store.current(identity(activity_id, "suggestion"), learner_id)
            try:
                body = apply_draft(
                    interpret(
                        fact,
                        current_feedback,
                        source_revision,
                        "final" if current_feedback else "provisional",
                    ),
                    draft,
                    [fb.body for fb in current_feedback],
                )
            except DraftMismatch as error:
                log.warning("evaluation activity=%s draft does not match the facts", activity_id)
                raise EvaluationUnavailable(str(error)) from error
            obj = document(
                "evaluation",
                identity(activity_id, "evaluation"),
                operation,
                learner_id,
                body,
                refs=(fact.ref, RevisionRef.model_validate(fact.body["course_ref"])),
                sources=tuple(fb.ref for fb in current_feedback),
                revision=prior.revision + 1 if prior else 1,
                source_revision=source_revision,
                occurred_at=fact.occurred_at,
            )
            suggestion = document(
                "suggestion",
                identity(activity_id, "suggestion"),
                operation,
                learner_id,
                next_suggestion(obj, self.next_lesson_material(learner_id)),
                refs=(obj.ref,),
                revision=prior_suggestion.revision + 1 if prior_suggestion else 1,
                source_revision=source_revision,
                occurred_at=fact.occurred_at,
            )
            self.save_step(
                operation,
                learner_id,
                "evaluation",
                [obj, suggestion],
                source_revision=source_revision,
                publications=(
                    publication(obj, "当前来源的限定范围解释"),
                    publication(suggestion, "候选建议，不能直接成为需求"),
                ),
            )
            self.evaluation_calls.append(
                {
                    "activity_id": activity_id,
                    "source_revision": source_revision,
                    "purpose": purpose,
                    "evidence": "simulated" if self.models.simulates("evaluation") else "model",
                }
            )
            self.ledger.finish(
                operation,
                learner_id,
                {
                    "evaluation_ref": obj.ref.model_dump(),
                    "suggestion_ref": suggestion.ref.model_dump(),
                },
            )
            return obj

        job = asyncio.create_task(generate())
        self.evaluation_jobs[job_key] = job
        job.add_done_callback(lambda completed: self.evaluation_jobs.pop(job_key, None))
        return await asyncio.shield(job)
