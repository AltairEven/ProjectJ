"""Simulation use cases. Each saved step can resume without rerunning successful work."""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe

from project_j.application.acceptance import simulation_evidence
from project_j.application.documents import document, identity
from project_j.application.ledger import Ledger, Request, Step
from project_j.classroom.harness import Harness
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.content import check_content
from project_j.evaluation.assessment import interpret, next_suggestion
from project_j.interface.submission import InputAdapter
from project_j.learning.adoption import adopt_demand, adopt_state, review_due
from project_j.learning.contracts import DemandBody, LearningState, PlanBody
from project_j.platform.files import PrivateFiles
from project_j.platform.retries import retry_save
from project_j.platform.simulated import SimulatedModel
from project_j.platform.storage import (
    Conflict,
    Publication,
    SaveKey,
    SimulationStore,
    canonical,
    digest,
)
from project_j.preparation.contracts import CourseBody
from project_j.preparation.packages import compile_course, material_source
from project_j.teacher.methods import JANE


class Coordinator:
    def __init__(
        self, store: SimulationStore, project_root: Path, verification: dict | None = None
    ):
        self.store, self.root = store, project_root
        self.files, self.ledger, self.model = PrivateFiles(store), Ledger(store), SimulatedModel()
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
            )
            return [self.store.read(ref, learner_id) for ref in receipt.records]
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
        receipt, attempts = retry_save(
            lambda: self.store.save(
                key, learner_id, records, tuple(publications), tuple(baselines)
            ),
            time.monotonic() + 10,
        )
        self.ledger.checkpoint(
            operation_id,
            learner_id,
            Step(
                step_id=step_id,
                source_revision=source_revision,
                owner=records[0].owner,
                input_digest=digest(canonical([ref.model_dump() for ref in baselines])),
                input_refs=tuple(baselines),
                attempts=attempts,
                execution="complete",
                generation="generated",
                save="succeeded",
                result_refs=receipt.records,
                effect_refs=receipt.activated,
            ),
        )
        return records

    def current(self, learner_id: str, label: str) -> Document:
        current = self.store.current(identity(learner_id, label), learner_id)
        if current is None:
            raise LookupError(f"missing effective {label}")
        return current

    def initialize(self, learner_id="fictional-L1"):
        from project_j.learning.catalog import INITIAL, define
        from project_j.learning.contracts import DimensionState, Goal, HouseholdBody, StateBody

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
        primary = Goal(
            dimension_id="english.reading.facts", description="观察故事事实、顺序与文本依据"
        )
        auxiliary = Goal(dimension_id="english.speaking.report", description="用短英语报告实际结果")
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
            operation, learner_id, {"demand_ref": demand[0].ref.model_dump(), "realm": "simulation"}
        )

    def prepare(self, request_id: str, learner_id: str, next_course=False):
        completed = self.ledger.completed_request(
            request_id, learner_id, "prepare", {"next_course": next_course}
        )
        if completed:
            return completed
        demand = self.current(learner_id, "demand")
        body = DemandBody.model_validate(demand.body)
        state, plan, household = [
            self.store.read(ref, learner_id)
            for ref in (body.state_ref, body.plan_ref, body.household_ref)
        ]
        for obj in (state, plan, household):
            if self.store.current(obj.object_id, learner_id).ref != obj.ref:
                raise Conflict("demand must be rechecked against current effective dependencies")
        operation = self.request(
            request_id,
            learner_id,
            "prepare",
            {"demand_ref": demand.ref.model_dump(), "next_course": next_course},
            sources=(demand.ref,),
            baselines=(demand.ref, state.ref, plan.ref, household.ref),
        )
        completed = self.ledger.result(operation, learner_id)
        if completed:
            return completed
        jane = self.store.current(identity("shared", "jane"), learner_id)
        if jane is None:
            jane = document("jane", identity("shared", "jane"), operation, None, JANE)
            self.save_step(
                operation,
                learner_id,
                "jane",
                [jane],
                publications=(Publication(ref=jane.ref, reason="唯一Jane模拟方法"),),
            )
        material = self.saved_step(operation, learner_id, "material")
        if material is None:
            source, text = material_source(self.root, next_course)
            asset = self.files.asset(text)
            obj = document(
                "material",
                identity(operation, "material"),
                operation,
                None,
                {
                    "status": "checked",
                    "asset": asset,
                    "source": source,
                    "source_sha256": digest(text),
                    "version": "1.1.0",
                    "audience": "student",
                    "visible_at": "task",
                    "limitations": ["自编材料；未测Lexile或RAZ，不证明科学实物定律。"],
                },
            )
            material = self.save_step(operation, learner_id, "material", [obj])
        material = material[0]
        package = self.saved_step(operation, learner_id, "package")
        if package is None:
            course_ref = RevisionRef(object_id=identity(operation, "course"), revision=1)
            brief_ref = RevisionRef(object_id=identity(operation, "brief"), revision=1)
            course = compile_course(
                body, demand.ref, jane.ref, brief_ref, material.ref, next_course
            )
            brief = document(
                "brief",
                brief_ref.object_id,
                operation,
                learner_id,
                {
                    "status": "checked",
                    "course_ref": course_ref.model_dump(),
                    "demand_ref": demand.ref.model_dump(),
                    "jane_ref": jane.ref.model_dump(),
                    "goals": [course.primary_goal.model_dump(), course.auxiliary_goal.model_dump()],
                    "history": "没有真实儿童历史，以明确模拟输入和初始未知状态开始。",
                    "path": [t.model_dump(mode="json") for t in course.tasks],
                    "budget": course.budget,
                    "adjustments": "挑战替换活动，降阶/跳过/暂停/停止遵守原目标与预算。",
                    "closing": "只总结实际范围，取得收到确认；停止不强迫小结。",
                },
                refs=(course_ref, demand.ref, jane.ref, material.ref),
            )
            obj = document(
                "course",
                course_ref.object_id,
                operation,
                learner_id,
                course,
                refs=(demand.ref, state.ref, plan.ref, jane.ref, brief_ref, material.ref),
            )
            package = self.save_step(
                operation,
                learner_id,
                "package",
                [obj, brief],
                publications=(
                    Publication(ref=obj.ref, reason="固定模拟课程包"),
                    Publication(ref=brief.ref, reason="同包简报合同"),
                ),
                baselines=(demand.ref, state.ref, plan.ref, household.ref, jane.ref),
            )
        course, brief = package
        checked = self.saved_step(operation, learner_id, "content-check")
        if checked is None:
            obj = document(
                "content_check",
                identity(operation, "content-check"),
                operation,
                learner_id,
                check_content(CourseBody.model_validate(course.body), material, self.files),
                refs=(course.ref, brief.ref, material.ref),
            )
            checked = self.save_step(
                operation,
                learner_id,
                "content-check",
                [obj],
                publications=(Publication(ref=obj.ref, reason="固定素材与任务依据已核对"),),
            )
        preview = self.saved_step(operation, learner_id, "preview")
        if preview is None:
            obj = document(
                "preview",
                identity(operation, "preview"),
                operation,
                learner_id,
                {
                    "status": "checked",
                    "course_ref": course.ref.model_dump(),
                    "content": course.body["parent_preview"],
                    "actual_read": "unknown",
                    "current_start": "pending",
                    "unverified": ["actual_api", "voice", "child_entry"],
                },
                refs=(course.ref, checked[0].ref),
            )
            preview = self.save_step(
                operation,
                learner_id,
                "preview",
                [obj],
                publications=(
                    Publication(ref=obj.ref, reason="生成同版本预览；不代表已阅读或开始"),
                ),
            )
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "course_ref": course.ref.model_dump(),
                "preview_ref": preview[0].ref.model_dump(),
                "check_ref": checked[0].ref.model_dump(),
                "preparation": "fixed_simulation_checked",
                "start": "pending",
                "unverified": ["actual_api", "voice", "child_entry"],
            },
        )

    def start(self, request_id: str, learner_id: str, course_ref: RevisionRef, conditions: dict):
        signature = {
            "learner_id": learner_id,
            "course_ref": course_ref.model_dump(),
            "conditions": conditions,
        }
        if request_id in self.start_requests:
            if self.start_requests[request_id]["signature"] != signature:
                raise Conflict("same start identity has different content")
            if self.core and self.core.mode != "ended":
                return self.start_requests[request_id]["result"]
            raise Conflict("original start ended; an explicit new request is required")
        if self.core and self.core.mode != "ended":
            raise Conflict("only one in-memory activity can run")
        if self.pending_seals:
            raise Conflict(
                "previous ended facts are still waiting for sealing or receipt confirmation"
            )
        if self.verification.get("result") != "pass":
            return {
                "decision": "waiting",
                "missing": ["build-bound simulation verification"],
                "unverified": ["actual_api", "voice", "child_entry"],
            }
        course = self.store.read(course_ref, learner_id)
        data = CourseBody.model_validate(course.body)
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
        restrictions = [
            doc
            for doc in self.store.list_current(learner_id, "restriction")
            if doc.body.get("restriction_state") == "active" and course.ref in doc.dependency_refs
        ]
        if not checks or restrictions:
            return {
                "decision": "blocked",
                "reason": "content check unavailable or affected restriction active",
            }
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
                "mode": "simulation_text_and_recognition_signals",
                "short_window_ms": 600,
                "explanation_window_ms": 1200,
                "actual_voice": "未验证",
                "automatic_real_voice": "disabled",
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
                "decision": "allow_simulation",
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
            ref.object_id: self.files.read_asset(self.store.read(ref, learner_id).body["asset"])
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
        self.core = Harness(activity_id, learner_id, course.ref, data, bindings, self.model)
        self.adapter = InputAdapter(self.core, self.late_recognition)
        self.store.active_activity_id = activity_id
        token = token_urlsafe(32)
        self.student_tokens[token] = activity_id
        result = {
            "decision": "allow_simulation",
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
        except OSError:
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

    def late_recognition(self, submitted):
        core = self.core
        if core is None or core.mode != "ended":
            return
        current = self.store.current(identity(core.activity_id, "fact"), core.learner_id)
        pending = self.freeze_end() if current is None else current
        reports = pending.body.get("late_recognition_revisions", [])
        if any(row["event_id"] == submitted.event_id for row in reports):
            return
        body = pending.body | {
            "late_recognition_revisions": reports + [submitted.model_dump(mode="json")],
            "attempts": core.attempts,
        }
        if current is None:
            self.pending_seals[core.activity_id] = document(
                "fact",
                pending.object_id,
                pending.operation_id,
                core.learner_id,
                body,
                refs=pending.dependency_refs,
                occurred_at=pending.occurred_at,
            )
            return
        operation = identity(core.activity_id, f"recognition-revision:{submitted.event_id}")
        updated = document(
            "fact",
            current.object_id,
            operation,
            core.learner_id,
            body,
            refs=current.dependency_refs,
            sources=(current.ref,),
            revision=current.revision + 1,
            source_revision=current.revision + 1,
            occurred_at=current.occurred_at,
        )
        self.store.save(
            SaveKey(
                operation_id=operation, step_id="fact-revision", source_revision=updated.revision
            ),
            core.learner_id,
            [updated],
            (
                Publication(
                    ref=updated.ref,
                    expected_revision=current.revision,
                    reason="后到原语段技术修订；同活动同尝试，不重开教学",
                ),
            ),
        )

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
        if corrections:
            for evaluation in self.store.list_current(learner_id, "evaluation"):
                if evaluation.body["activity_id"] == activity_id:
                    self.restrict(
                        operation, learner_id, evaluation, "新更正影响旧解释，待同活动来源复核。"
                    )
        return self.ledger.finish(
            operation,
            learner_id,
            {"feedback_ref": saved[0].ref.model_dump(), "evaluation": "not_requested"},
        )

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
        fact = self.activity_facts(activity_id, learner_id)
        source_revision, feedbacks = self.source_revision(activity_id, learner_id)
        existing = [
            doc
            for doc in self.store.list_current(learner_id, "evaluation")
            if doc.body["activity_id"] == activity_id and doc.source_revision == source_revision
        ]
        if existing:
            return existing[-1]
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
            prior = [
                doc
                for doc in self.store.list_current(learner_id, "evaluation")
                if doc.body["activity_id"] == activity_id
            ]
            no_new_facts = bool(feedbacks) and all(
                fb.body["text"].strip() == "无额外补充" and not fb.body["corrections"]
                for fb in feedbacks
            )
            if not (prior and no_new_facts):
                await self.model.evaluate(activity_id, source_revision)
            current_revision, current_feedback = self.source_revision(activity_id, learner_id)
            if current_revision != source_revision:
                # Re-enter with the new source, never publish the late old interpretation.
                return await self.evaluate(
                    f"{request_id}:source:{current_revision}", learner_id, activity_id, purpose
                )
            obj = document(
                "evaluation",
                identity(activity_id, f"evaluation:{source_revision}"),
                operation,
                learner_id,
                interpret(
                    fact,
                    current_feedback,
                    source_revision,
                    "final" if current_feedback else "provisional",
                ),
                refs=(fact.ref, RevisionRef.model_validate(fact.body["course_ref"])),
                sources=tuple(fb.ref for fb in current_feedback),
                source_revision=source_revision,
                occurred_at=fact.occurred_at,
            )
            suggestion = document(
                "suggestion",
                identity(activity_id, f"suggestion:{source_revision}"),
                operation,
                learner_id,
                next_suggestion(obj),
                refs=(obj.ref,),
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
                    Publication(ref=obj.ref, reason="当前来源的限定范围解释"),
                    Publication(ref=suggestion.ref, reason="候选建议，不能直接成为需求"),
                ),
            )
            self.evaluation_calls.append(
                {
                    "activity_id": activity_id,
                    "source_revision": source_revision,
                    "purpose": purpose,
                    "evidence": "simulated",
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

    def adopt(self, request_id: str, learner_id: str, evaluation: Document):
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
        self.ledger.checkpoint(
            operation,
            learner_id,
            Step(
                step_id=f"plan-recheck:{plan.revision}",
                source_revision=current_revision,
                owner="learning",
                input_digest=digest(canonical([plan.ref.model_dump()])),
                input_refs=(plan.ref,),
                attempts=1,
                execution="complete",
                save="succeeded",
                effect_refs=(plan.ref,),
                required_input=("沿用当前周计划：既定目标与预算未发生必要变化。",),
            ),
        )
        suggestion = self.store.current(
            identity(evaluation.body["activity_id"], f"suggestion:{current_revision}"), learner_id
        )
        if suggestion is None:
            raise LookupError("saved candidate suggestion missing")
        demand = self.saved_step(operation, learner_id, "demand", current_revision)
        if demand is None:
            old = self.current(learner_id, "demand")
            data = adopt_demand(
                DemandBody.model_validate(old.body), state.ref, plan.ref, suggestion
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
            demand = self.save_step(
                operation,
                learner_id,
                "demand",
                [obj],
                source_revision=current_revision,
                publications=(
                    Publication(
                        ref=obj.ref,
                        expected_revision=old.revision,
                        reason="综合当前状态/计划，采用建议并记录理由",
                    ),
                ),
                baselines=(old.ref, state.ref, plan.ref, data.household_ref),
            )
        return self.ledger.finish(
            operation,
            learner_id,
            {
                "state_ref": state.ref.model_dump(),
                "plan_ref": plan.ref.model_dump(),
                "plan_effect": "reused_with_reason",
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
        adopted = self.adopt(f"{request_id}:adopt", learner_id, evaluation)
        package = self.prepare(f"{request_id}:prepare", learner_id, next_course=True)
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
                "course": "fixed_checked",
                "unknown": ["独立条件、保持、迁移、真实声音、儿童与教学效果未验证。"],
            },
            refs=(
                evaluation.ref,
                RevisionRef.model_validate(adopted["demand_ref"]),
                RevisionRef.model_validate(package["course_ref"]),
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
            "realm": "simulation",
            "learner_id": learner_id,
            "qualification": "not_granted",
            "actual_api": "未验证",
            "actual_voice": "未验证",
            "verification": self.verification,
            "objects": [doc.model_dump(mode="json") for doc in documents],
            "review_due": review_due(
                [doc for doc in documents if doc.kind == "plan"], datetime.now(UTC)
            ),
            "model_calls": self.model.calls,
            "pending_seals": list(self.pending_seals),
        }
