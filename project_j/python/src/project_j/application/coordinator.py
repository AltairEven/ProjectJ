"""The application coordinator: the one runtime object and its operation and save mechanics
(each saved step can resume without rerunning successful work). The use cases live by business
in preparing, teaching, evaluating and adopting; the fixture learner in learning/fixtures."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe

from sqlalchemy.exc import SQLAlchemyError

from project_j.application.acceptance import simulation_evidence
from project_j.application.adopting import Adopting
from project_j.application.documents import document, identity, publication
from project_j.application.evaluating import Evaluating
from project_j.application.ledger import Ledger, Request, Step
from project_j.application.preparing import Preparing
from project_j.application.progress import advance, jobs, lesson_evaluations, next_step
from project_j.application.teaching import Teaching
from project_j.classroom.harness import Harness
from project_j.contracts import Document, RevisionRef
from project_j.evaluation.scoring import lesson_scores
from project_j.interface.submission import InputAdapter
from project_j.learning.adoption import review_due
from project_j.learning.fixtures import initialize
from project_j.platform.files import PrivateFiles
from project_j.platform.models.service import ModelService
from project_j.platform.speech.contracts import PronunciationScorer
from project_j.platform.speech.simulated import SimulatedScorer
from project_j.platform.storage import (
    Conflict,
    SaveKey,
    SimulationStore,
    canonical,
    digest,
)
from project_j.platform.tts.contracts import Synthesizer
from project_j.platform.tts.simulated import SimulatedSynthesizer

log = logging.getLogger(__name__)


class Coordinator(Preparing, Teaching, Evaluating, Adopting):
    def __init__(
        self,
        store: SimulationStore,
        project_root: Path,
        verification: dict | None = None,
        models: ModelService | None = None,
        scorer: PronunciationScorer | None = None,
        synthesizer: Synthesizer | None = None,
        onboarding: bool | None = None,
    ):
        self.store, self.root = store, project_root
        # A study space starts at onboarding; a simulation may (ADR-0018). Tests of a study
        # space may pass False to use the fixture learner instead.
        self.onboarding = store.realm == "study" if onboarding is None else onboarding
        # One learner per data space.
        self.learner_id = "learner" if store.realm == "study" else "fictional-L1"
        self.files, self.ledger = PrivateFiles(store), Ledger(store)
        self.models = models or ModelService.simulated()
        self.scorer = scorer or SimulatedScorer()
        self.synthesizer = synthesizer or SimulatedSynthesizer()
        self.verification = (
            verification if verification is not None else simulation_evidence(project_root)
        )
        self.core: Harness | None = None
        self.adapter: InputAdapter | None = None
        self.token = token_urlsafe(32)  # The one operator's capability, management and class.
        self.start_requests: dict[str, dict] = {}
        self.material_cache: dict[str, str] = {}
        self.recorded: dict[str, bytes] = {}  # Adopted speech of the class, by exact text.
        self.visuals: dict[str, tuple[bytes, str]] = {}  # Adopted student visuals, by id.
        self.evaluation_jobs: dict[tuple, asyncio.Task] = {}
        # Background AI work by name (application/progress.py); failures wait for a retry.
        self.jobs: dict[str, asyncio.Task] = {}
        self.job_failures: dict[str, str] = {}
        self.job_progress: dict[str, str] = {}
        self.evaluation_calls: list[dict] = []
        self.pending_seals: dict[str, Document] = {}

    def request(
        self,
        request_id: str,
        learner_id: str,
        purpose: str,
        body: dict,
        sources=(),
        source_revision=1,
        baselines=(),
    ) -> str:
        return self.ledger.accept(
            Request(
                caller="simulation_parent",
                request_id=request_id,
                learner_id=learner_id,
                purpose=purpose,
                body=body,
                source_refs=tuple(sources),
                source_revision=source_revision,
                expected_baselines=tuple(baselines),
            )
        )

    def saved_step(self, operation_id: str, learner_id: str, step_id: str, source_revision=1):
        key = (
            SaveKey(operation_id=operation_id, step_id=step_id, source_revision=source_revision)
            if source_revision is not None
            else None
        )
        receipt = (
            self.store.receipt(key, learner_id)
            if key
            else self.store.find_receipt(operation_id, step_id, learner_id)
        )
        if receipt:
            successful = [
                row["payload"]
                for row in self.ledger.steps(operation_id, learner_id)
                if row["step_id"] == step_id
                and row["source_revision"] == receipt.source_revision
                and row["payload"]["execution"] == "complete"
            ]
            if successful:
                return [self.store.read(ref, learner_id) for ref in receipt.records]
            self.ledger.checkpoint(
                operation_id,
                learner_id,
                Step(
                    step_id=step_id,
                    source_revision=receipt.source_revision,
                    owner=self.store.read(receipt.records[0], learner_id).owner,
                    input_digest=digest(
                        canonical([ref.model_dump() for ref in receipt.baseline_refs])
                    ),
                    input_refs=receipt.baseline_refs,
                    attempts=1,
                    execution="complete",
                    generation="generated",
                    save="succeeded",
                    result_refs=receipt.records,
                    effect_refs=receipt.activated,
                ),
                replace_unfinished=True,
            )
            return [self.store.read(ref, learner_id) for ref in receipt.records]
        for row in self.ledger.steps(operation_id, learner_id):
            if row["step_id"] == step_id and (
                source_revision is None or row["source_revision"] == source_revision
            ):
                step = Step.model_validate(row["payload"])
                if step.execution == "complete":
                    if step.save != "not_requested" or not step.effect_refs:
                        raise Conflict("completed save step has no durable receipt")
                    return [self.store.read(ref, learner_id) for ref in step.effect_refs]
        return None

    def save_step(
        self,
        operation_id: str,
        learner_id: str,
        step_id: str,
        records: list[Document],
        source_revision=1,
        publications=(),
        baselines=(),
    ):
        key = SaveKey(operation_id=operation_id, step_id=step_id, source_revision=source_revision)
        step = Step(
            step_id=step_id,
            source_revision=source_revision,
            owner=records[0].owner,
            input_digest=digest(canonical([ref.model_dump() for ref in baselines])),
            input_refs=tuple(baselines),
            attempts=1,
            deadline=datetime.now(UTC) + timedelta(seconds=10),
            execution="complete",
            generation="generated",
            save="succeeded",
        )
        # Validate step identity/inputs before any business revision can become effective.
        self.ledger.checkpoint(
            operation_id,
            learner_id,
            step.model_copy(update={"execution": "running", "save": "not_requested"}),
            replace_unfinished=True,
        )
        try:
            receipt = self.store.save(
                key, learner_id, records, tuple(publications), tuple(baselines)
            )
        except (OSError, SQLAlchemyError) as error:
            # Unknown result: query the save unit first; only a missing receipt is a failure.
            try:
                receipt, save = self.store.receipt(key, learner_id), "failed"
            except OSError, SQLAlchemyError:
                receipt, save = None, "unknown"
            if receipt is None:
                self.note_failure(operation_id, learner_id, step, save, type(error).__name__)
                raise
        self.ledger.checkpoint(
            operation_id,
            learner_id,
            step.model_copy(
                update={"result_refs": receipt.records, "effect_refs": receipt.activated}
            ),
            replace_unfinished=True,
        )
        return records

    def note_failure(self, operation_id, learner_id, step, save, failure):
        try:
            self.ledger.checkpoint(
                operation_id,
                learner_id,
                step.model_copy(
                    update={
                        "execution": "failed",
                        "generation": "generated",
                        "save": save,
                        "failure": failure,
                        "next_step": step.step_id,
                        "required_input": (
                            "Send again with the same request_id; saved steps are not redone.",
                        ),
                    }
                ),
                replace_unfinished=True,
            )
        except OSError, SQLAlchemyError:
            pass  # The ledger shares the failing store; the caller still receives the error.

    def current(self, learner_id: str, label: str) -> Document:
        current = self.store.current(identity(learner_id, label), learner_id)
        if current is None:
            raise LookupError(f"missing effective {label}")
        return current

    def restrict(self, operation_id: str, learner_id: str, target: Document, reason: str):
        label = f"restriction:{target.object_id}:{target.revision}"
        saved = self.saved_step(operation_id, learner_id, label)
        if saved:
            return saved[0]
        obj = document(
            "restriction",
            identity(operation_id, label),
            operation_id,
            learner_id,
            {
                "status": "checked",
                "restriction_state": "active",
                "target_ref": target.ref.model_dump(),
                "reason": reason,
                "owner": target.owner,
                "release": "Lifted after the owner checks the exact version and current evidence.",
            },
            refs=(target.ref,),
        )
        self.save_step(
            operation_id,
            learner_id,
            label,
            [obj],
            publications=(publication(obj, "Scoped impact restriction"),),
        )
        return obj

    def dashboard(self, learner_id: str):
        self.store.ensure_idle()
        initialize(self, learner_id)
        documents = self.store.list_current(learner_id)
        advance(self, learner_id)
        return {
            "realm": self.store.realm,
            "learner_id": learner_id,
            "next": next_step(self, learner_id),
            "jobs": jobs(self),
            "qualification": "not_granted",
            "actual_api": "unverified",
            "actual_voice": "unverified",
            "verification": self.verification,
            "objects": [doc.model_dump(mode="json") for doc in documents],
            # The version each rewritten evaluation replaced, for the before/after view.
            "previous_evaluations": [
                self.store.read(
                    RevisionRef(object_id=doc.object_id, revision=doc.revision - 1), learner_id
                ).model_dump(mode="json")
                for doc in documents
                if doc.kind == "evaluation" and doc.revision > 1
            ],
            "review_due": review_due(
                [doc for doc in documents if doc.kind == "plan"], datetime.now(UTC)
            ),
            "model_calls": self.models.calls,
            "models": self.models.status(),
            "pending_seals": list(self.pending_seals),
            "lost_activities": self.lost_activities(learner_id, documents),
            # Each lesson's averages by task type and ability, oldest lesson first (ADR-0020).
            "scores": [
                lesson_scores(doc.body) | {"occurred_at": doc.occurred_at.isoformat()}
                for doc in lesson_evaluations(self, learner_id)
                if doc.occurred_at
            ],
        }

    def lost_activities(self, learner_id: str, documents: list[Document]) -> list[dict]:
        """A saved start only proves a request; without a fact or memory its class is a gap."""
        known = {doc.body["activity_id"] for doc in documents if doc.kind == "fact"}
        known |= set(self.pending_seals) | ({self.core.activity_id} if self.core else set())
        reports = [doc for doc in documents if doc.kind == "parent_report"]
        return [
            {
                "activity_id": start.body["planned_activity_id"],
                "start_ref": start.ref.model_dump(),
                "course_ref": start.body["course_ref"],
                "requested_at": start.created_at.isoformat(),
                "state": "The program stopped: what happened and how the class ended are unknown; "
                "unsaved class content cannot be restored.",
                "reports": [
                    {"text": doc.body["text"], "reported_at": doc.body["reported_at"]}
                    for doc in reports
                    if doc.body["activity_id"] == start.body["planned_activity_id"]
                ],
            }
            for start in self.store.list_saved(learner_id, "start")
            if start.body["planned_activity_id"] not in known
        ]
