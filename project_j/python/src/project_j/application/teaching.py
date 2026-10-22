"""Coordinator use cases of the class: starting it, its in-memory core and sealing what
happened."""

import logging

from sqlalchemy.exc import SQLAlchemyError

from project_j.application.documents import document, identity, publication, successor
from project_j.application.ledger import Request
from project_j.application.preparing import STALE_COURSE
from project_j.classroom.harness import Harness
from project_j.contracts import RevisionRef
from project_j.curriculum.corrections import active_restrictions
from project_j.interface.submission import InputAdapter
from project_j.platform.speech.contracts import ScorerNotReady
from project_j.platform.storage import (
    Conflict,
    SaveKey,
)
from project_j.platform.tts.contracts import SynthesizerNotReady
from project_j.preparation.contracts import CourseBody
from project_j.preparation.packages import student_text
from project_j.preparation.reading import words
from project_j.preparation.resources import adopted, ready_resources

log = logging.getLogger(__name__)


class Teaching:
    async def confirm_and_start(
        self,
        request_id: str,
        learner_id: str,
        course_ref: RevisionRef,
        materials_ready: bool,
        on_progress=None,
    ) -> dict:
        """The student's one click: confirm this version, make and adopt its resources, start."""
        course = CourseBody.model_validate(self.store.read(course_ref, learner_id).body)
        offscreen = any(task.purpose == "offscreen" for task in course.tasks)
        if offscreen and not materials_ready:
            raise Conflict(
                'This lesson uses real things. Get them ready, tick "Things are ready", then start.'
            )
        if self.verification.get("result") != "pass":  # start() would refuse after resources
            log.info("start refused reason=unchecked_build")
            raise Conflict("This version has not passed the checks. Run the checks, then start.")
        self.confirm(f"{request_id}:confirm", learner_id, course_ref)
        failed = await ready_resources(self, learner_id, course_ref, on_progress)
        if failed:
            raise Conflict(f"Resources not ready: {', '.join(failed)}. You can try again.")
        result = self.start(f"{request_id}:start", learner_id, course_ref, materials_ready)
        if result["decision"] != f"allow_{self.store.realm}":
            reason = result.get("reason") or ", ".join(result.get("missing", ()))
            raise Conflict(f"The class cannot start: {reason}")
        return result

    def start(
        self,
        request_id: str,
        learner_id: str,
        course_ref: RevisionRef,
        materials_ready: bool = False,  # The student's own word; asked only when needed.
    ):
        signature = {
            "learner_id": learner_id,
            "course_ref": course_ref.model_dump(),
            "materials_ready": materials_ready,
        }
        if request_id in self.start_requests:
            if self.start_requests[request_id]["signature"] != signature:
                raise Conflict("same start identity has different content")
            original = self.start_requests[request_id]["result"]
            if (
                self.core
                and self.core.mode != "ended"
                and self.core.activity_id == original["snapshot"]["activity_id"]
            ):
                return original
            raise Conflict("original start ended; an explicit new request is required")
        if self.core and self.core.mode != "ended":
            raise Conflict("only one in-memory activity can run")
        if self.pending_seals:
            raise Conflict(
                "previous ended facts are still waiting for sealing or receipt confirmation"
            )
        self.models.require()
        if self.verification.get("result") != "pass":
            return {
                "decision": "waiting",
                "missing": ["build-bound simulation verification"],
                "unverified": ["actual_api", "voice"],
            }
        course = self.store.read(course_ref, learner_id)
        data = CourseBody.model_validate(course.body)
        if any(task.purpose == "read_aloud" for task in data.tasks) and (
            reasons := self.scorer.reasons()
        ):
            raise ScorerNotReady(
                "Pronunciation scoring is not ready: "
                + "; ".join(reasons)
                + ". Run project-j-scorer-init probe."
            )
        # Every class delivers Jane by voice; there is no text-only class mode yet.
        if reasons := self.synthesizer.reasons():
            raise SynthesizerNotReady(
                "Speech is not ready: " + "; ".join(reasons) + ". Run project-j-tts-init probe."
            )
        # Materials are asked for only when the course uses them; nobody is asked to accompany.
        if any(task.purpose == "offscreen" for task in data.tasks) and not materials_ready:
            return {"decision": "waiting", "missing": ["materials"]}
        demand = self.current(learner_id, "demand")
        if demand.ref != data.demand_ref:
            raise Conflict("course uses an old demand")
        # A profile edit after preparation does not void the course (it shapes the next one).
        baseline_refs = (demand.ref, data.state_ref, data.plan_ref, data.jane_ref)
        for ref in baseline_refs:
            current = self.store.current(ref.object_id, learner_id)
            if current is None or current.ref != ref:
                raise Conflict("start baseline changed", current.ref if current else None)
        checks = [
            doc
            for doc in self.store.list_current(learner_id, "content_check")
            if course.ref in doc.dependency_refs
        ]
        restrictions = active_restrictions(
            self,
            learner_id,
            (
                course.ref,
                *baseline_refs,
                data.brief_ref,
                *data.material_refs,
                *(doc.ref for doc in checks),
            ),
        )
        if not checks or restrictions:
            return {
                "decision": "blocked",
                "reason": "content check unavailable or affected restriction active",
            }
        latest = self.store.current(course_ref.object_id, learner_id)
        if latest is None or latest.ref != course_ref:
            raise Conflict(STALE_COURSE)
        confirmed = next(
            (
                preview
                for preview in self.store.list_current(learner_id, "preview")
                if (preview.body.get("confirmation") or {}).get("course_ref")
                == course_ref.model_dump()
            ),
            None,
        )
        if confirmed is None:
            log.info("start refused reason=unconfirmed")
            raise Conflict("Confirm the lesson first; then the class can start.")
        recorded, visuals, missing, skipped = adopted(self, confirmed)
        if missing:
            log.info("start refused reason=resources_missing count=%d", len(missing))
            raise Conflict(
                f"Resources not ready: {', '.join(missing)}. Make, check and adopt them in the "
                "lesson plan, then start."
            )
        operation = self.request(
            request_id,
            learner_id,
            "start",
            signature,
            sources=(course.ref,),
            baselines=baseline_refs,
        )
        # A persisted start is only a request, never a replayable record of an actual class.
        if self.ledger.result(operation, learner_id):
            return {
                "decision": "interrupted_unknown",
                "reason": "memory core is absent; do not reopen from the old request",
            }
        activity_id = identity(operation, "activity")
        input_policy = document(
            "input_policy",
            identity(operation, "input-policy"),
            operation,
            None,
            {
                "status": "checked",
                "mode": "text_and_reading_recording",
                "actual_voice": "unverified",
                "real_recording": "disabled" if self.scorer.simulated else "enabled",
            },
        )
        # The bindings and voice this class actually runs with, never declared defaults.
        models = {
            purpose: binding.model_dump() | {"simulated": self.models.simulates(purpose)}
            for purpose, binding in self.models.bindings.items()
        }
        config = document(
            "config",
            identity(operation, "config"),
            operation,
            None,
            {
                "status": "checked",
                "realm": self.store.realm,
                "models": models,
                "pronunciation": "simulated" if self.scorer.simulated else "local",
                "synthesis": "simulated" if self.synthesizer.simulated else "local",
            },
        )
        log.info(
            "start config realm=%s efforts=%s simulated=%d/%d",
            self.store.realm,
            ",".join(f"{purpose}={item['effort']}" for purpose, item in models.items()),
            sum(item["simulated"] for item in models.values()),
            len(models),
        )
        verification = document(
            "validation",
            identity(operation, "validation"),
            operation,
            None,
            {
                "status": "checked",
                "report": self.verification,
                "scope": "simulation_only",
                "qualification": "not_granted",
            },
            refs=(config.ref, input_policy.ref),
        )
        start = document(
            "start",
            identity(operation, "start"),
            operation,
            learner_id,
            {
                "status": "checked",
                "decision": f"allow_{self.store.realm}",
                "planned_activity_id": activity_id,
                "materials_ready": materials_ready,
                "course_ref": course.ref.model_dump(),
                "actual_activity": "unknown_until_memory_core_exists",
            },
            refs=(
                course.ref,
                demand.ref,
                checks[0].ref,
                verification.ref,
                config.ref,
                input_policy.ref,
            ),
        )
        self.save_step(
            operation, learner_id, "start-check", [input_policy, config, verification, start]
        )
        self.ledger.finish(
            operation,
            learner_id,
            {"planned_activity_id": activity_id, "actual_activity": "unknown_after_restart"},
        )
        self.material_cache = {
            ref.object_id: student_text(
                self.files.read_asset(self.store.read(ref, learner_id).body["asset"])
            )
            for ref in data.material_refs
        }
        # The same course is taught again when the student retakes a stopped class.
        attempt = 1 + sum(
            fact.body["course_ref"] == course.ref.model_dump()
            for fact in self.store.list_current(learner_id, "fact")
        )
        # A review or a retake is not a first reading of the story (ADR-0022).
        first_reading = data.lesson_type != "review" and attempt == 1
        # The student text's first line is the title; the story is counted like preparation.
        story_words = sum(
            len(words(text.partition("\n")[2])) for text in self.material_cache.values()
        )
        bindings = {
            "course": course.ref.model_dump(),
            "demand": demand.ref.model_dump(),
            "state": data.state_ref.model_dump(),
            "plan": data.plan_ref.model_dump(),
            "jane": data.jane_ref.model_dump(),
            "profile": self.current(learner_id, "profile").ref.model_dump(),
            "config": config.ref.model_dump(),
            "input_policy": input_policy.ref.model_dump(),
            "validation": verification.ref.model_dump(),
            "start": start.ref.model_dump(),
        }
        self.core = Harness(
            activity_id,
            learner_id,
            course.ref,
            data,
            bindings,
            self.models,
            self.scorer,
            realm=self.store.realm,
            jane=self.store.read(data.jane_ref, learner_id).body,
            voiced=frozenset(recorded),
            visible=tuple(item for item in data.resources if item.resource_id in visuals),
            story_words=story_words,
            first_reading=first_reading,
        )
        # Held in memory for the class; nothing is read from disk while teaching.
        self.recorded, self.visuals = recorded, visuals
        log.info(
            "start resources recorded=%d visuals=%d skipped=%d attempt=%d "
            "story_words=%d first_reading=%s",
            len(recorded),
            len(visuals),
            skipped,
            attempt,
            story_words,
            first_reading,
        )
        self.adapter = InputAdapter(self.core)
        self.store.active_activity_id = activity_id
        result = {"decision": f"allow_{self.store.realm}", "snapshot": self.core.snapshot()}
        self.start_requests[request_id] = {"signature": signature, "result": result}
        return result

    def classroom(self, token: str):
        """The class in memory, for the operator's token only."""
        if token != self.token or self.core is None:
            raise PermissionError("no class in memory for this capability")
        return self.core

    def student_view(self, core: Harness):
        if core is not self.core:
            raise PermissionError("view is not bound to the current memory activity")
        # The story shows from "Start reading" on, except while reading is paused (ADR-0022).
        shown = core.course.material_refs if core.story_visible() else ()
        return core.snapshot() | {
            "materials": {ref.object_id: self.material_cache[ref.object_id] for ref in shown}
        }

    def seal(self):
        if self.core is None or self.core.mode != "ended":
            raise ValueError("fixed classroom end required; pause does not seal")
        core = self.core
        self.store.active_activity_id = None
        operation = identity(core.activity_id, "seal-operation")
        key = SaveKey(operation_id=operation, step_id="fact", source_revision=1)
        receipt = self.store.receipt(key, core.learner_id)
        if receipt:
            fact = self.store.read(receipt.records[0], core.learner_id)
        else:
            fact = self.freeze_end()
            # Save first; a missing coordinator ledger cannot prevent factual sealing.
            self.store.save(
                key,
                core.learner_id,
                [fact],
                (publication(fact, "Sealed fact checked for structure; no model evaluation"),),
            )
        self.pending_seals.pop(core.activity_id, None)
        log.info(
            "seal activity=%s events=%d attempts=%d end=%s receipt=%s",
            core.activity_id,
            len(fact.body["events"]),
            len(fact.body["attempts"]),
            fact.body["end"]["reason"],
            "existing" if receipt else "new",
        )
        try:
            self.ledger.accept(
                Request(
                    caller="classroom_end",
                    request_id=core.end["end_id"],
                    learner_id=core.learner_id,
                    purpose="seal",
                    body={"activity_id": core.activity_id, "fact_ref": fact.ref.model_dump()},
                    source_refs=(fact.ref,),
                ),
                planned_operation_id=operation,
            )
            self.ledger.finish(
                operation, core.learner_id, {"fact_ref": fact.ref.model_dump(), "save": "succeeded"}
            )
        except OSError, SQLAlchemyError:
            # Facts already have a durable receipt; progress failure cannot undo them.
            pass
        return fact

    def freeze_end(self):
        core = self.core
        if core is None or core.mode != "ended":
            raise ValueError("a fixed end boundary is required")
        self.store.active_activity_id = None
        fact = self.pending_seals.get(core.activity_id)
        if fact is None:
            fact = document(
                "fact",
                identity(core.activity_id, "fact"),
                identity(core.activity_id, "seal-operation"),
                core.learner_id,
                core.facts(),
                refs=tuple(RevisionRef.model_validate(value) for value in core.bindings.values()),
                occurred_at=core.started_at,
            )
            self.pending_seals[core.activity_id] = fact
        return fact

    def late_delivery(self, feedback):
        core = self.core
        if core is None or core.mode != "ended":
            return
        current = self.store.current(identity(core.activity_id, "fact"), core.learner_id)
        if current is None:
            pending = self.freeze_end()
            reports = pending.body.setdefault("late_delivery_reports", [])
            if not any(row["delivery_event_id"] == feedback.delivery_event_id for row in reports):
                reports.append(feedback.model_dump(mode="json"))
            return
        operation = identity(core.activity_id, f"late-delivery:{feedback.delivery_event_id}")
        key = SaveKey(
            operation_id=operation, step_id="fact-revision", source_revision=current.revision + 1
        )
        if any(
            row["delivery_event_id"] == feedback.delivery_event_id
            for row in current.body.get("late_delivery_reports", [])
        ):
            return
        updated = successor(
            current,
            operation,
            current.body
            | {
                "late_delivery_reports": current.body.get("late_delivery_reports", [])
                + [feedback.model_dump(mode="json")]
            },
            refs=current.dependency_refs,
            sources=(current.ref,),
            source_revision=current.revision + 1,
            occurred_at=current.occurred_at,
        )
        self.store.save(
            key,
            core.learner_id,
            [updated],
            (
                publication(
                    updated, "Late delivery receipt added as a revision; teaching not reopened"
                ),
            ),
        )
