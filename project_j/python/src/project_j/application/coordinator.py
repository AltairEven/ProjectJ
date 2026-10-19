"""Simulation use cases. Each saved step can resume without rerunning successful work."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe

from sqlalchemy.exc import SQLAlchemyError

from project_j.application.acceptance import simulation_evidence
from project_j.application.documents import document, identity
from project_j.application.ledger import Ledger, Request, Step
from project_j.classroom.harness import Harness
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.content import check_content
from project_j.curriculum.corrections import active_restrictions, affected
from project_j.evaluation.assessment import (
    DraftMismatch,
    EvaluationDraft,
    EvaluationUnavailable,
    apply_draft,
    evaluation_request,
    interpret,
    next_suggestion,
)
from project_j.interface.submission import InputAdapter
from project_j.learning.adoption import adopt_state, review_due
from project_j.learning.contracts import DemandBody, Goal, LearningState, PlanBody
from project_j.learning.planning import (
    PlanningUnavailable,
    apply_demand_draft,
    demand_request,
    week_plan_after,
)
from project_j.platform.files import PrivateFiles
from project_j.platform.models.service import ModelService
from project_j.platform.retries import retry_save
from project_j.platform.speech.contracts import PronunciationScorer, ScorerNotReady
from project_j.platform.speech.simulated import SimulatedScorer
from project_j.platform.storage import (
    Conflict,
    Publication,
    SaveKey,
    SimulationStore,
    canonical,
    digest,
)
from project_j.platform.tts.contracts import Synthesizer, SynthesizerNotReady
from project_j.platform.tts.simulated import SimulatedSynthesizer
from project_j.preparation.contracts import CourseBody
from project_j.preparation.drafting import (
    DraftRejected,
    PreparationUnavailable,
    checked_course,
    discussion_request,
    preparation_request,
    repair_request,
)
from project_j.preparation.packages import (
    COURSES,
    FIRST,
    library_entry,
    next_material,
    template_draft,
)
from project_j.teacher.methods import JANE

PRIMARY_GOAL = Goal(
    dimension_id="english.reading.facts", description="观察故事事实、顺序与文本依据"
)
AUXILIARY_GOAL = Goal(dimension_id="english.speaking.report", description="用短英语报告实际结果")

log = logging.getLogger(__name__)


def student_text(source: str) -> str:
    """Only the story reaches the student; the source's provenance line is adult metadata."""
    # ponytail: assumes the fixed course format ("# title", one 素材版本 line); a structured
    # student field on the material object is the upgrade path for other formats.
    lines = [
        line.removeprefix("# ") for line in source.splitlines() if not line.startswith("素材版本：")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def story_text(source: str) -> str:
    """The story a course is compiled from: the student text without its title line."""
    return student_text(source).split("\n", 1)[1]


STALE_COURSE = "课程已有更新版本或需求已变，请刷新后对当前版本操作。"


def publications(objects: list[Document], revised: dict[str, int] | None = None):
    """Course, brief, check and preview become effective together; material needs no publication."""
    reasons = {
        "course": "备课模型编写、规则核对的课程",
        "brief": "同包简报合同",
        "content_check": "原文依据与结构已核对",
        "preview": "生成同版本预览；不代表已阅读、确认或开始",
    }
    return tuple(
        Publication(
            ref=obj.ref,
            expected_revision=(revised or {}).get(obj.object_id),
            reason=reasons[obj.kind],
        )
        for obj in objects
        if obj.kind in reasons
    )


class Coordinator:
    def __init__(
        self,
        store: SimulationStore,
        project_root: Path,
        verification: dict | None = None,
        models: ModelService | None = None,
        scorer: PronunciationScorer | None = None,
        synthesizer: Synthesizer | None = None,
    ):
        self.store, self.root = store, project_root
        self.files, self.ledger = PrivateFiles(store), Ledger(store)
        self.models = models or ModelService.simulated()
        self.scorer = scorer or SimulatedScorer()
        self.synthesizer = synthesizer or SimulatedSynthesizer()
        self.verification = (
            verification if verification is not None else simulation_evidence(project_root)
        )
        self.core: Harness | None = None
        self.adapter: InputAdapter | None = None
        self.student_tokens: dict[str, str] = {}
        self.parent_token = token_urlsafe(32)
        self.start_requests: dict[str, dict] = {}
        self.material_cache: dict[str, str] = {}
        self.evaluation_jobs: dict[tuple, asyncio.Task] = {}
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
            receipt, attempts = retry_save(
                lambda: self.store.save(
                    key, learner_id, records, tuple(publications), tuple(baselines)
                ),
                time.monotonic() + 10,
            )
        except (OSError, SQLAlchemyError) as error:
            # Unknown result: query the save unit first; only a missing receipt is a failure.
            attempts = getattr(error, "attempts", 1)
            try:
                receipt, save = self.store.receipt(key, learner_id), "failed"
            except OSError, SQLAlchemyError:
                receipt, save = None, "unknown"
            if receipt is None:
                self.note_failure(
                    operation_id, learner_id, step, attempts, save, type(error).__name__
                )
                raise
        self.ledger.checkpoint(
            operation_id,
            learner_id,
            step.model_copy(
                update={
                    "attempts": attempts,
                    "result_refs": receipt.records,
                    "effect_refs": receipt.activated,
                }
            ),
            replace_unfinished=True,
        )
        return records

    def note_failure(self, operation_id, learner_id, step, attempts, save, failure):
        try:
            self.ledger.checkpoint(
                operation_id,
                learner_id,
                step.model_copy(
                    update={
                        "attempts": attempts,
                        "execution": "failed",
                        "generation": "generated",
                        "save": save,
                        "failure": failure,
                        "next_step": step.step_id,
                        "required_input": ("用同一request_id重发；成功步骤不重做。",),
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

    def initialize(self, learner_id="fictional-L1"):
        from project_j.learning.catalog import INITIAL, define
        from project_j.learning.contracts import DimensionState, HouseholdBody, StateBody

        operation = self.request(
            f"initialize:{learner_id}", learner_id, "initialize", {"fixture_version": 1}
        )
        existing = self.ledger.result(operation, learner_id)
        if existing:
            return existing
        catalog_refs = tuple(
            RevisionRef.model_validate(
                define(
                    self, f"initial-catalog:{learner_id}:{entry.dimension_id}", learner_id, entry
                )["capability_ref"]
            )
            for entry in INITIAL
        )
        primary, auxiliary = PRIMARY_GOAL, AUXILIARY_GOAL
        state = self.saved_step(operation, learner_id, "state")
        if state is None:
            body = StateBody(
                status="checked",
                dimensions=tuple(
                    DimensionState(
                        dimension_id=goal.dimension_id,
                        task_scope=goal.description,
                        judgment="unknown",
                        help="unknown",
                        material_condition="尚未观察",
                        observed_at=None,
                        limitations=("明确模拟初始条件，没有真实儿童学习样本。",),
                    )
                    for goal in (
                        primary,
                        auxiliary,
                        Goal(
                            dimension_id="science.observation.report",
                            description="实际科学条件与结果报告",
                        ),
                    )
                ),
            )
            obj = document(
                "state",
                identity(learner_id, "state"),
                operation,
                learner_id,
                body,
                refs=catalog_refs,
            )
            state = self.save_step(
                operation,
                learner_id,
                "state",
                [obj],
                publications=(Publication(ref=obj.ref, reason="模拟初始未知状态"),),
            )
        state = state[0]
        household = self.saved_step(operation, learner_id, "household")
        if household is None:
            obj = document(
                "household",
                identity(learner_id, "household"),
                operation,
                None,
                HouseholdBody(
                    status="checked",
                    raw_report="模拟：有30分钟、纸、书和小车，家长可陪伴。",
                    provider="模拟家长",
                    reported_at=datetime.now(UTC),
                    applies_to=(learner_id,),
                    limitations=("虚构条件；真实家庭与设备未验证。",),
                ),
            )
            household = self.save_step(
                operation,
                learner_id,
                "household",
                [obj],
                publications=(Publication(ref=obj.ref, reason="明确共享范围的模拟条件"),),
            )
        household = household[0]
        parent = None
        for level in ("long_term", "annual", "stage", "week"):
            result = self.saved_step(operation, learner_id, f"plan:{level}")
            if result is None:
                body = PlanBody(
                    status="checked",
                    level=level,
                    parent_ref=parent.ref if parent else None,
                    main_goal=primary,
                    budget_minutes=30,
                    basis_refs=(state.ref,),
                    verification="首次、帮助后、延迟保持与新材料分别观察，不预填成绩。",
                    exit_conditions="疲惫或停止则结束，不自动加时或产生补课债务。",
                    review_at=None,
                    limitations=("模拟方向与近期安排；不预排十年每日课程。",),
                )
                obj = document(
                    "plan",
                    identity(learner_id, f"plan:{level}"),
                    operation,
                    learner_id,
                    body,
                    refs=(state.ref,) + ((parent.ref,) if parent else ()),
                )
                result = self.save_step(
                    operation,
                    learner_id,
                    f"plan:{level}",
                    [obj],
                    publications=(Publication(ref=obj.ref, reason="模拟计划检查与层次连接"),),
                )
            parent = result[0]
        demand = self.saved_step(operation, learner_id, "demand")
        if demand is None:
            body = DemandBody(
                status="checked",
                state_ref=state.ref,
                plan_ref=parent.ref,
                household_ref=household.ref,
                primary_goal=primary,
                auxiliary_goal=auxiliary,
                budget_minutes=30,
                prerequisites=("相关阅读、表达和材料经历未知，用短观察取样。",),
                sampling_requirements=("保留未提示初答与帮助后新尝试。",),
                help_requirements=("两次求助用不同指导；未知外部帮助不填独立。",),
                offscreen_requirements="离屏停止采集，返回取得实际条件/结果报告。",
                fallback="已核实不依赖声音评价的任务可使用文字模拟。",
                gaps=("真实API、声音与儿童用途未验证。",),
            )
            obj = document(
                "demand",
                identity(learner_id, "demand"),
                operation,
                learner_id,
                body,
                refs=(state.ref, parent.ref, household.ref),
            )
            demand = self.save_step(
                operation,
                learner_id,
                "demand",
                [obj],
                publications=(Publication(ref=obj.ref, reason="模拟需求已复核"),),
                baselines=(state.ref, parent.ref, household.ref),
            )
        return self.ledger.finish(
            operation,
            learner_id,
            {"demand_ref": demand[0].ref.model_dump(), "realm": self.store.realm},
        )

    def chosen_material(self, learner_id: str, demand: DemandBody) -> tuple[str, bool]:
        """Preparation follows the material named by the suggestion the demand adopted."""
        for choice in demand.suggestion_choices:
            if choice.action in {"adopt", "modify"}:
                chosen = self.store.read(choice.suggestion_ref, learner_id).body["material"]
                return chosen["source"], chosen["familiar"]
        return FIRST, False

    def next_lesson_material(self, learner_id: str) -> dict:
        # The next lesson follows the latest lesson, even when an older one is re-evaluated.
        facts = self.store.list_current(learner_id, "fact")
        latest = max(facts, key=lambda fact: fact.occurred_at)
        return next_material(self.taught_source(learner_id, latest), self.read_sources(learner_id))

    def read_sources(self, learner_id: str) -> set[str]:
        # ponytail: any sealed fact counts its story as read, even a class stopped before
        # the task; this only errs toward review, never toward new material.
        facts = self.store.list_current(learner_id, "fact")
        return {self.taught_source(learner_id, fact) for fact in facts}

    def taught_source(self, learner_id: str, fact: Document) -> str:
        course = self.store.read(RevisionRef.model_validate(fact.body["course_ref"]), learner_id)
        material_ref = RevisionRef.model_validate(course.body["material_refs"][0])
        return self.store.read(material_ref, learner_id).body["source"]

    async def prepare(self, request_id: str, learner_id: str):
        completed = self.ledger.completed_request(request_id, learner_id, "prepare", {})
        if completed:
            course = self.store.read(
                RevisionRef.model_validate(completed["course_ref"]), learner_id
            )
            if active_restrictions(self, learner_id, (course.ref, *course.dependency_refs)):
                raise Conflict(
                    "saved preparation has restricted dependencies; owner recheck required"
                )
            return completed
        self.models.require()
        demand = self.current(learner_id, "demand")
        body = DemandBody.model_validate(demand.body)
        state, plan, household = [
            self.store.read(ref, learner_id)
            for ref in (body.state_ref, body.plan_ref, body.household_ref)
        ]
        for obj in (state, plan, household):
            if self.store.current(obj.object_id, learner_id).ref != obj.ref:
                raise Conflict("demand must be rechecked against current effective dependencies")
        if active_restrictions(self, learner_id, (demand.ref, state.ref, plan.ref, household.ref)):
            raise Conflict("preparation dependencies are restricted; owner recheck required")
        operation = self.request(
            request_id,
            learner_id,
            "prepare",
            {"demand_ref": demand.ref.model_dump()},
            sources=(demand.ref,),
            baselines=(demand.ref, state.ref, plan.ref, household.ref),
        )
        completed = self.ledger.result(operation, learner_id)
        if completed:
            return completed
        jane = self.store.current(identity("shared", "jane"), learner_id)
        if jane and active_restrictions(self, learner_id, (jane.ref,)):
            raise Conflict("selected Jane version is restricted; owner recheck required")
        if jane is None:
            jane = document("jane", identity("shared", "jane"), operation, None, JANE)
            self.save_step(
                operation,
                learner_id,
                "jane",
                [jane],
                publications=(Publication(ref=jane.ref, reason="唯一Jane模拟方法"),),
            )
        package = self.saved_step(operation, learner_id, "package")
        if package is None:
            source, familiar = self.chosen_material(learner_id, body)
            read = self.read_sources(learner_id)
            library = self.library(read)
            goals = (body.primary_goal.dimension_id, body.auxiliary_goal.dimension_id)
            template = (
                template_draft(body, source, library[source]["story"], familiar)
                if goals == (PRIMARY_GOAL.dimension_id, AUXILIARY_GOAL.dimension_id)
                else None
            )
            request = preparation_request(
                body,
                state.body,
                household.body,
                [doc.body for doc in self.store.list_current(learner_id, "capability")],
                jane.body,
                list(library.values()),
                source,
                template,
            )
            refs = {
                "course": RevisionRef(object_id=identity(operation, "course"), revision=1),
                "brief": RevisionRef(object_id=identity(operation, "brief"), revision=1),
                "material": RevisionRef(object_id=identity(operation, "material"), revision=1),
            }
            checked, draft = await self.draft_course(
                request, "prepare", demand, jane, refs, library, read
            )
            objects = self.package_objects(
                operation, learner_id, checked, draft, demand, (state, plan), jane, refs, []
            )
            package = self.save_step(
                operation,
                learner_id,
                "package",
                objects,
                publications=publications(objects),
                baselines=(demand.ref, state.ref, plan.ref, household.ref, jane.ref),
            )
        course, _, _, checked, preview = package
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "course_ref": course.ref.model_dump(),
                "preview_ref": preview.ref.model_dump(),
                "check_ref": checked.ref.model_dump(),
                "preparation": "drafted",
                "start": "pending",
                "unverified": ["actual_api", "voice", "child_entry"],
            },
        )

    def current_package(self, learner_id: str, course_ref: RevisionRef):
        """The current course version under the current demand, and its preview."""
        course = self.store.current(course_ref.object_id, learner_id)
        demand = self.current(learner_id, "demand")
        if (
            course is None
            or course.ref != course_ref
            or course.body["demand_ref"] != demand.ref.model_dump()
        ):
            raise Conflict(STALE_COURSE)
        previews = [
            doc
            for doc in self.store.list_current(learner_id, "preview")
            if doc.body["course_ref"] == course_ref.model_dump()
        ]
        return course, demand, previews[0]

    async def discuss(self, request_id: str, learner_id: str, course_ref: RevisionRef, feedback):
        """The parent's feedback on one course version; the model revises it into the next."""
        signature = {"course_ref": course_ref.model_dump(), "feedback": feedback}
        completed = self.ledger.completed_request(request_id, learner_id, "discuss", signature)
        if completed:
            return completed
        self.models.require()
        # Saved steps are looked up before the staleness check: this round's own revision
        # makes course_ref stale, and a retry must still resume it.
        operation = self.request(
            request_id,
            learner_id,
            "discuss",
            signature,
            sources=(course_ref,),
            baselines=(course_ref, self.current(learner_id, "demand").ref),
        )
        completed = self.ledger.result(operation, learner_id)
        if completed:
            return completed
        material = self.store.read(
            RevisionRef.model_validate(
                self.store.read(course_ref, learner_id).body["material_refs"][0]
            ),
            learner_id,
        )
        saved = self.saved_step(operation, learner_id, "revision")
        if saved is None:
            course, demand, preview = self.current_package(learner_id, course_ref)
            body = DemandBody.model_validate(demand.body)
            state, plan, household = [
                self.store.read(ref, learner_id)
                for ref in (body.state_ref, body.plan_ref, body.household_ref)
            ]
            jane = self.store.read(RevisionRef.model_validate(course.body["jane_ref"]), learner_id)
            read = self.read_sources(learner_id)
            library = self.library(read)
            base = preparation_request(
                body,
                state.body,
                household.body,
                [doc.body for doc in self.store.list_current(learner_id, "capability")],
                jane.body,
                list(library.values()),
                self.chosen_material(learner_id, body)[0],
                None,
            )
            request = discussion_request(
                base, preview.body["draft"], feedback, preview.body["discussion"]
            )
            brief_ref = RevisionRef.model_validate(course.body["brief_ref"])
            # ponytail: the material gets a new revision every round even when the story stays;
            # the asset itself is shared by checksum.
            refs = {
                "course": RevisionRef(object_id=course.object_id, revision=course.revision + 1),
                "brief": RevisionRef(
                    object_id=brief_ref.object_id, revision=brief_ref.revision + 1
                ),
                "material": RevisionRef(
                    object_id=material.object_id, revision=material.revision + 1
                ),
            }
            checked, draft = await self.draft_course(
                request, "discuss", demand, jane, refs, library, read
            )
            discussion = [*preview.body["discussion"], {"feedback": feedback, "note": draft.note}]
            objects = self.package_objects(
                operation, learner_id, checked, draft, demand, (state, plan), jane, refs, discussion
            )
            saved = self.save_step(
                operation,
                learner_id,
                "revision",
                objects,
                publications=publications(
                    objects,
                    {course.object_id: course.revision, brief_ref.object_id: brief_ref.revision},
                ),
                baselines=(course.ref, preview.ref, demand.ref),
            )
        new_course, _, new_material, check, new_preview = saved
        log.info(
            "preparation discussed course=%s revision=%d material=%s",
            new_course.object_id,
            new_course.revision,
            "same"
            if new_material.body["source_sha256"] == material.body["source_sha256"]
            else "changed",
        )
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "course_ref": new_course.ref.model_dump(),
                "preview_ref": new_preview.ref.model_dump(),
                "check_ref": check.ref.model_dump(),
                "preparation": "drafted",
                "start": "pending",
                "unverified": ["actual_api", "voice", "child_entry"],
            },
        )

    def confirm(self, request_id: str, learner_id: str, course_ref: RevisionRef):
        """The parent confirms one exact course version; resources wait for generation."""
        signature = {"course_ref": course_ref.model_dump()}
        completed = self.ledger.completed_request(request_id, learner_id, "confirm", signature)
        if completed:
            return completed
        course, demand, preview = self.current_package(learner_id, course_ref)
        operation = self.request(
            request_id,
            learner_id,
            "confirm",
            signature,
            sources=(course.ref,),
            baselines=(course.ref, preview.ref),
        )
        completed = self.ledger.result(operation, learner_id)
        if completed:
            return completed
        saved = self.saved_step(operation, learner_id, "confirmation")
        if saved is None and preview.body.get("confirmation"):
            saved = [preview]  # Already confirmed: another request does not add a revision.
        if saved is None:
            obj = document(
                "preview",
                preview.object_id,
                operation,
                learner_id,
                preview.body
                | {
                    "confirmation": {
                        "state": "confirmed",
                        "course_ref": course.ref.model_dump(),
                        "confirmed_at": datetime.now(UTC).isoformat(),
                    },
                    "resources": [
                        item | {"state": "awaiting_generation"}
                        for item in preview.body["resources"]
                    ],
                },
                refs=preview.dependency_refs,
                revision=preview.revision + 1,
            )
            saved = self.save_step(
                operation,
                learner_id,
                "confirmation",
                [obj],
                publications=(
                    Publication(
                        ref=obj.ref,
                        expected_revision=preview.revision,
                        reason="家长确认这一课程版本；资源待生成",
                    ),
                ),
                baselines=(course.ref, preview.ref),
            )
        resources = len(saved[0].body["resources"])
        log.info(
            "preparation confirmed course=%s revision=%d resources=%d",
            course.object_id,
            course.revision,
            resources,
        )
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "course_ref": course.ref.model_dump(),
                "preview_ref": saved[0].ref.model_dump(),
                "confirmation": "confirmed",
                "resources": "awaiting_generation" if resources else "none",
            },
        )

    def library(self, read: set[str]) -> dict[str, dict]:
        return {
            source: library_entry(
                source, story_text((self.root / source).read_text()), source in read
            )
            for source in COURSES
        }

    async def draft_course(self, request, stage, demand, jane, refs, library, read):
        """One model draft, one repair round on rule problems; nothing is saved here."""
        subject = "课程" if stage == "prepare" else "修改"
        body = DemandBody.model_validate(demand.body)
        catalog = {
            doc.body["dimension_id"]
            for doc in self.store.list_current(demand.learner_id, "capability")
        }
        for attempt in (1, 2):
            reply = await self.models.run(request)
            if not reply.ok:
                log.warning("preparation failure stage=%s failure=%s", stage, reply.failure)
                raise PreparationUnavailable(reply.failure, subject)
            draft = reply.output
            try:
                checked = checked_course(
                    draft,
                    body,
                    demand.ref,
                    jane.ref,
                    refs["brief"],
                    refs["material"],
                    library,
                    read,
                    catalog,
                )
            except DraftRejected as rejected:
                codes = ",".join(dict.fromkeys(code for code, _ in rejected.problems))
                log.warning(
                    "preparation refused stage=%s attempt=%d checks=%s", stage, attempt, codes
                )
                reasons = [text for _, text in rejected.problems]
                if attempt == 2:
                    raise PreparationUnavailable(
                        "规则核对未通过：" + "；".join(reasons[:3]), subject
                    ) from None
                request = repair_request(request, draft, reasons)
                continue
            log.info(
                "preparation drafted stage=%s attempt=%d material=%s tasks=%d read_aloud=%d "
                "resources=%d",
                stage,
                attempt,
                "library" if checked.source else "model",
                len(draft.tasks),
                len(checked.course.tasks) - len(draft.tasks),
                len(draft.resources),
            )
            return checked, draft
        raise AssertionError("unreachable")

    def package_objects(
        self, operation, learner_id, checked, draft, demand, sources, jane, refs, discussion
    ) -> list[Document]:
        """Course, brief, material, content check and preview of one checked draft."""
        state, plan = sources
        if checked.source:
            text = (self.root / checked.source).read_text()
            origin, source = "library", checked.source
            limitation = "自编材料；未测Lexile或RAZ，不证明科学实物定律。"
        else:
            text = f"# {checked.title}\n\n{checked.story}\n"
            origin, source = "model", f"model:{digest(text)}"
            limitation = "模型自编；未测Lexile或RAZ，只经规则核对依据原文，不证明科学实物定律。"
        material = document(
            "material",
            refs["material"].object_id,
            operation,
            None,
            {
                "status": "checked",
                "asset": self.files.asset(text),
                "source": source,
                "origin": origin,
                "title": checked.title,
                "source_sha256": digest(text),
                "version": "1.1.0" if origin == "library" else "1.0.0",
                "audience": "student",
                "visible_at": "task",
                "limitations": [limitation],
            },
            revision=refs["material"].revision,
        )
        course_body = checked.course
        course = document(
            "course",
            refs["course"].object_id,
            operation,
            learner_id,
            course_body,
            refs=(demand.ref, state.ref, plan.ref, jane.ref, refs["brief"], material.ref),
            revision=refs["course"].revision,
        )
        brief = document(
            "brief",
            refs["brief"].object_id,
            operation,
            learner_id,
            {
                "status": "checked",
                "course_ref": course.ref.model_dump(),
                "demand_ref": demand.ref.model_dump(),
                "jane_ref": jane.ref.model_dump(),
                "goals": [
                    course_body.primary_goal.model_dump(),
                    course_body.auxiliary_goal.model_dump(),
                ],
                "history": draft.brief.history,
                "path": [t.model_dump(mode="json") for t in course_body.tasks],
                "budget": course_body.budget,
                "adjustments": draft.brief.adjustments,
                "closing": draft.brief.closing,
            },
            refs=(course.ref, demand.ref, jane.ref, material.ref),
            revision=refs["brief"].revision,
        )
        check = document(
            "content_check",
            identity(operation, "content-check"),
            operation,
            learner_id,
            check_content(
                course_body, DemandBody.model_validate(demand.body), material, self.files
            ),
            refs=(course.ref, brief.ref, material.ref),
        )
        preview = document(
            "preview",
            identity(operation, "preview"),
            operation,
            learner_id,
            {
                "status": "checked",
                "course_ref": course.ref.model_dump(),
                "content": course_body.parent_preview,
                "note": draft.note,
                "resources": [
                    item.model_dump(mode="json") | {"state": "planned"}
                    for item in course_body.resources
                ],
                "discussion": discussion,
                "confirmation": None,
                # A discussion round revises this draft instead of rebuilding it from the course.
                "draft": draft.model_dump(mode="json"),
                "actual_read": "unknown",
                "current_start": "pending",
                "unverified": ["actual_api", "voice", "child_entry"],
            },
            refs=(course.ref, check.ref),
        )
        return [course, brief, material, check, preview]

    def start(self, request_id: str, learner_id: str, course_ref: RevisionRef, conditions: dict):
        signature = {
            "learner_id": learner_id,
            "course_ref": course_ref.model_dump(),
            "conditions": conditions,
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
                "unverified": ["actual_api", "voice", "child_entry"],
            }
        course = self.store.read(course_ref, learner_id)
        data = CourseBody.model_validate(course.body)
        if any(task.purpose == "read_aloud" for task in data.tasks) and (
            reasons := self.scorer.reasons()
        ):
            raise ScorerNotReady(
                "发音评分未就绪：" + "；".join(reasons) + "。请运行 project-j-scorer-init probe。"
            )
        # Every class delivers Jane by voice; there is no text-only class mode yet.
        if reasons := self.synthesizer.reasons():
            raise SynthesizerNotReady(
                "语音合成未就绪：" + "；".join(reasons) + "。请运行 project-j-tts-init probe。"
            )
        required = {
            "materials": "reported_available",
            "time": "reported_30_minutes",
            "accompaniment": "reported_present",
        }
        if any(conditions.get(name) == "unavailable" for name in required):
            return {"decision": "blocked", "reason": "required household condition failed"}
        missing = [name for name, expected in required.items() if conditions.get(name) != expected]
        if conditions.get("explicit_start") is not True:
            missing.append("explicit_start")
        if not conditions.get("raw_report"):
            missing.append("raw_report")
        if missing:
            return {"decision": "waiting", "missing": missing}
        demand = self.current(learner_id, "demand")
        if demand.ref != data.demand_ref:
            raise Conflict("course uses an old demand")
        demand_body = DemandBody.model_validate(demand.body)
        baseline_refs = (
            demand.ref,
            data.state_ref,
            data.plan_ref,
            demand_body.household_ref,
            data.jane_ref,
        )
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
        if not any(
            (preview.body.get("confirmation") or {}).get("course_ref") == course_ref.model_dump()
            for preview in self.store.list_current(learner_id, "preview")
        ):
            log.info("start refused reason=unconfirmed")
            raise Conflict("课程未确认：请先在本课安排里确认课程，确认后才能开始。")
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
                "mode": "simulation_text_and_reading_recording",
                "actual_voice": "未验证",
                "real_recording": "disabled",
            },
        )
        config = document(
            "config",
            identity(operation, "config"),
            operation,
            None,
            {
                "status": "checked",
                "provider": "simulation",
                "model": "gpt-6.1-sol",
                "classroom_effort": "low",
                "preparation_effort": "medium",
                "evaluation_effort": "medium",
                "real_api": "disabled",
                "real_voice": "disabled",
            },
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
                "conditions": conditions,
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
        bindings = {
            "course": course.ref.model_dump(),
            "demand": demand.ref.model_dump(),
            "state": data.state_ref.model_dump(),
            "plan": data.plan_ref.model_dump(),
            "jane": data.jane_ref.model_dump(),
            "household": demand_body.household_ref.model_dump(),
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
        )
        self.adapter = InputAdapter(self.core)
        self.store.active_activity_id = activity_id
        token = token_urlsafe(32)
        self.student_tokens[token] = activity_id
        result = {
            "decision": f"allow_{self.store.realm}",
            "student_token": token,
            "snapshot": self.core.snapshot(),
        }
        self.start_requests[request_id] = {"signature": signature, "result": result}
        return result

    def student(self, token: str):
        if self.core is None or self.student_tokens.get(token) != self.core.activity_id:
            raise PermissionError("student capability does not cover the current activity")
        return self.core

    def student_view(self, core: Harness):
        if core is not self.core:
            raise PermissionError("view is not bound to the current memory activity")
        snapshot = core.snapshot()
        allowed = {
            ref.object_id
            for action in core.actions.values()
            if action.action_id in core.delivered or action.action_id == core.pending
            for ref in action.material_refs
        }
        return snapshot | {
            "materials": {identity: self.material_cache[identity] for identity in allowed}
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
                (Publication(ref=fact.ref, reason="结束事实结构核对；无模型评价"),),
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
        updated = document(
            "fact",
            current.object_id,
            operation,
            core.learner_id,
            current.body
            | {
                "late_delivery_reports": current.body.get("late_delivery_reports", [])
                + [feedback.model_dump(mode="json")]
            },
            refs=current.dependency_refs,
            sources=(current.ref,),
            revision=current.revision + 1,
            source_revision=current.revision + 1,
            occurred_at=current.occurred_at,
        )
        self.store.save(
            key,
            core.learner_id,
            [updated],
            (
                Publication(
                    ref=updated.ref,
                    expected_revision=current.revision,
                    reason="后到技术交付回执追加修订；不重开教学",
                ),
            ),
        )

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
                    "provider": "simulated_parent",
                    "occurred_at": None,
                    "reported_at": datetime.now(UTC).isoformat(),
                    "corrections": list(corrections),
                    "limitations": ["无额外补充不等于独立；模拟来源不转为儿童证据。"],
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
                publications=(
                    Publication(ref=obj.ref, reason="追加原活动来源，不制造新活动或尝试"),
                ),
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
                    "provider": "simulated_parent",
                    "reported_at": datetime.now(UTC).isoformat(),
                    "gap": lost["state"],
                    "limitations": [
                        "家长有限报告是新的实际来源；不补造原话、不重复计样本，不自动进入评价。"
                    ],
                },
                refs=(start_ref,),
            )
            report = self.save_step(
                operation,
                learner_id,
                "parent-report",
                [obj],
                publications=(Publication(ref=obj.ref, reason="只保存家长补记，缺口保留"),),
            )
        return self.ledger.finish(operation, learner_id, {"report_ref": report[0].ref.model_dump()})

    def uses_corrected_source(self, learner_id: str, doc: Document, activity_id: str) -> bool:
        """Only this activity's interpretation and current or untaught derived objects use it;
        later lessons' own evidence and lessons already taught stay valid history."""
        if doc.kind in {"evaluation", "report"}:
            return doc.body.get("activity_id") == activity_id
        if doc.kind == "suggestion":
            return doc.body["evaluation_ref"]["object_id"] == identity(activity_id, "evaluation")
        if doc.kind in {"state", "demand", "plan", "household"}:
            return self.store.current(doc.object_id, learner_id).ref == doc.ref
        if doc.kind == "start":
            return False
        taught = {
            fact.body["course_ref"]["object_id"]
            for fact in self.store.list_current(learner_id, "fact")
        }
        package = {doc.object_id} | {ref.object_id for ref in doc.dependency_refs}
        return not package & taught

    def later_feedback(self, request_id: str, learner_id: str, activity_id: str):
        fact = self.activity_facts(activity_id, learner_id)
        operation = self.request(
            request_id,
            learner_id,
            "later-feedback",
            {"activity_id": activity_id},
            sources=(fact.ref,),
        )
        existing = self.ledger.result(operation, learner_id)
        if existing:
            return existing
        obj = document(
            "report",
            identity(operation, "feedback-choice"),
            operation,
            learner_id,
            {
                "status": "checked",
                "activity_id": activity_id,
                "later_explicit": True,
                "evaluation": "not_requested",
            },
            refs=(fact.ref,),
        )
        self.save_step(
            operation,
            learner_id,
            "later-feedback",
            [obj],
            publications=(Publication(ref=obj.ref, reason="仅记录明确稍后，不请求模型"),),
        )
        return self.ledger.finish(
            operation,
            learner_id,
            {"choice_ref": obj.ref.model_dump(), "evaluation": "not_requested"},
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
        if not feedbacks:
            later = any(
                doc.body.get("activity_id") == activity_id and doc.body.get("later_explicit")
                for doc in self.store.list_current(learner_id, "report")
            )
            if not later or purpose not in {"next_course", "workbench_evaluation"}:
                raise ValueError(
                    "sealing, refresh or later choice alone must not trigger evaluation"
                )
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
                    Publication(
                        ref=obj.ref,
                        expected_revision=prior.revision if prior else None,
                        reason="当前来源的限定范围解释",
                    ),
                    Publication(
                        ref=suggestion.ref,
                        expected_revision=prior_suggestion.revision if prior_suggestion else None,
                        reason="候选建议，不能直接成为需求",
                    ),
                ),
            )
            self.evaluation_calls.append(
                {
                    "activity_id": activity_id,
                    "source_revision": source_revision,
                    "purpose": purpose,
                    "evidence": "simulated" if self.evaluation_simulated() else "model",
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

    def evaluation_simulated(self) -> bool:
        binding = self.models.bindings.get("evaluation")
        adapter = self.models.adapters.get(binding.adapter) if binding else None
        return adapter is None or adapter.simulated

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
                obj = document(
                    "state",
                    old.object_id,
                    operation,
                    learner_id,
                    next_body,
                    revision=old.revision + 1,
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
                        Publication(
                            ref=obj.ref,
                            expected_revision=old.revision,
                            reason="只采用评价支持维度，保持未知",
                        ),
                    ),
                    baselines=(old.ref,),
                )
        state = state[0]
        if self.current(learner_id, "state").ref != state.ref:
            # Later evidence survives; retry continues against the currently valid state.
            state = self.current(learner_id, "state")
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
            current = DemandBody.model_validate(old.body)
            household = self.store.read(current.household_ref, learner_id)
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
                    household.body,
                    catalog,
                    suggestion.body,
                    old.body,
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
                    new_plan = document(
                        "plan",
                        plan.object_id,
                        operation,
                        learner_id,
                        adjusted,
                        revision=plan.revision + 1,
                        source_revision=current_revision,
                        refs=plan.dependency_refs,
                        sources=(evaluation.ref,),
                        occurred_at=evaluation.occurred_at,
                    )
                    objects.append(new_plan)
                    publications.append(
                        Publication(
                            ref=new_plan.ref,
                            expected_revision=plan.revision,
                            reason=f"授权内周计划小调整：{result.output.plan_reason}",
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
                )
            except PlanningUnavailable as error:
                log.warning("planning activity=%s rejected: %s", activity_id, error)
                raise
            baselines = (old.ref, state.ref, week_before.ref, data.household_ref)
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
            obj = document(
                "demand",
                old.object_id,
                operation,
                learner_id,
                data,
                revision=old.revision + 1,
                source_revision=current_revision,
                refs=(state.ref, plan.ref, data.household_ref, suggestion.ref),
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
                    Publication(
                        ref=obj.ref,
                        expected_revision=old.revision,
                        reason="规划模型提出、规则核对后的下节需求",
                    ),
                    *publications,
                ),
                baselines=baselines,
            )
            log.info(
                "planning activity=%s goals=%s/%s action=%s plan_effect=%s",
                activity_id,
                data.primary_goal.dimension_id,
                data.auxiliary_goal.dimension_id,
                result.output.suggestion_action,
                data.plan_effect,
            )
        body = demand[0].body
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

    async def next_course(self, request_id: str, learner_id: str, activity_id: str):
        completed = self.ledger.completed_request(
            request_id, learner_id, "next-package-report", {"activity_id": activity_id}
        )
        if completed:
            return completed
        evaluation = await self.evaluate(
            f"{request_id}:evaluate", learner_id, activity_id, "next_course"
        )
        adopted = await self.adopt(f"{request_id}:adopt", learner_id, evaluation)
        try:
            package = await self.prepare(f"{request_id}:prepare", learner_id)
        except PreparationUnavailable as gap:
            package = {"status": "not_ready", "reason": str(gap)}
        operation = self.request(
            request_id,
            learner_id,
            "next-package-report",
            {"activity_id": activity_id},
            sources=(evaluation.ref,),
            source_revision=evaluation.source_revision,
        )
        existing = self.ledger.result(operation, learner_id)
        if existing:
            return existing
        obj = document(
            "report",
            identity(operation, "report"),
            operation,
            learner_id,
            {
                "status": "checked",
                "activity_id": activity_id,
                "evaluation_ref": evaluation.ref.model_dump(),
                "adoption": adopted,
                "next_package": package,
                "facts": "saved",
                "feedback": "saved"
                if self.feedbacks(activity_id, learner_id)
                else "later_explicit",
                "evaluation": "saved",
                "state_demand": "effective",
                "course": "drafted" if "course_ref" in package else "not_ready",
                "unknown": ["独立条件、保持、迁移、真实声音、儿童与教学效果未验证。"],
            },
            refs=(
                evaluation.ref,
                RevisionRef.model_validate(adopted["demand_ref"]),
                *(
                    (RevisionRef.model_validate(package["course_ref"]),)
                    if "course_ref" in package
                    else ()
                ),
            ),
            source_revision=evaluation.source_revision,
        )
        self.save_step(
            operation,
            learner_id,
            "report",
            [obj],
            source_revision=evaluation.source_revision,
            publications=(Publication(ref=obj.ref, reason="汇总实际成果；报告不控制上游生效"),),
        )
        return self.ledger.finish(
            operation,
            learner_id,
            {"report_ref": obj.ref.model_dump(), "next_package": package, "adoption": adopted},
        )

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
                "release": "主责复核确切版本及当前依据后解除。",
            },
            refs=(target.ref,),
        )
        self.save_step(
            operation_id,
            learner_id,
            label,
            [obj],
            publications=(Publication(ref=obj.ref, reason="有范围的影响限制"),),
        )
        return obj

    def dashboard(self, learner_id: str):
        self.store.ensure_idle()
        self.initialize(learner_id)
        documents = self.store.list_current(learner_id)
        return {
            "realm": self.store.realm,
            "learner_id": learner_id,
            "qualification": "not_granted",
            "actual_api": "未验证",
            "actual_voice": "未验证",
            "verification": self.verification,
            "objects": [doc.model_dump(mode="json") for doc in documents],
            "review_due": review_due(
                [doc for doc in documents if doc.kind == "plan"], datetime.now(UTC)
            ),
            "model_calls": self.models.calls,
            "models": self.models.status(),
            "pending_seals": list(self.pending_seals),
            "lost_activities": self.lost_activities(learner_id, documents),
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
                "state": "进程丢失：实际发生与结束未知，未保存的课堂内容不可恢复。",
                "reports": [
                    {"text": doc.body["text"], "reported_at": doc.body["reported_at"]}
                    for doc in reports
                    if doc.body["activity_id"] == start.body["planned_activity_id"]
                ],
            }
            for start in self.store.list_saved(learner_id, "start")
            if start.body["planned_activity_id"] not in known
        ]
