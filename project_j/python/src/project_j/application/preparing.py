"""Coordinator use cases of preparation: choosing the story, drafting, discussing and
confirming a course."""

import logging
from datetime import UTC, date, datetime

from project_j.application.documents import document, identity, publication, successor
from project_j.application.progress import latest_facts, lesson_score_summary
from project_j.contracts import Document, RevisionRef
from project_j.curriculum.content import check_content
from project_j.curriculum.corrections import active_restrictions
from project_j.learning.contracts import DemandBody
from project_j.learning.fixtures import AUXILIARY_GOAL, PRIMARY_GOAL
from project_j.learning.vocabulary import VocabularyBody, candidates
from project_j.platform.storage import (
    Conflict,
    digest,
)
from project_j.preparation.contracts import CourseBody, StoryLevel
from project_j.preparation.difficulty import story_level
from project_j.preparation.drafting import (
    DraftRejected,
    PreparationUnavailable,
    checked_course,
    discussion_request,
    preparation_request,
    repair_request,
)
from project_j.preparation.folders import draft_of, story_path
from project_j.preparation.packages import (
    COURSES,
    FIRST,
    library_entry,
    next_material,
    story_text,
    template_draft,
)
from project_j.preparation.resources import GENERATED, planned_resources, ready_resources
from project_j.preparation.task_types import calibration, timing_samples
from project_j.teacher.methods import JANE

log = logging.getLogger(__name__)


STALE_COURSE = "This lesson has a newer version or its plan changed. Refresh and try again."


def publications(objects: list[Document]):
    """Course, brief, check and preview become effective together; material needs no publication."""
    reasons = {
        "course": "Lesson written by the model and checked by the rules",
        "brief": "Brief of the same package",
        "content_check": "Story evidence and structure checked",
        "preview": "Preview of the same version; not read, confirmed or started yet",
    }
    return tuple(publication(obj, reasons[obj.kind]) for obj in objects if obj.kind in reasons)


class Preparing:
    @staticmethod
    def offered_vocabulary(
        body: VocabularyBody | None, *, placement: bool, familiar: bool, today: date
    ) -> list[dict] | None:
        """Marked words for a new story; none for the placement test or a review of a read story."""
        if placement or familiar:
            return None
        return candidates(body, today) if body else []

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

    def recent_titles(self, learner_id: str, limit: int = 3) -> list[str]:
        """The latest lessons' story titles, so the next one turns to another interest."""
        titles = []
        for fact in latest_facts(self, learner_id)[:limit]:
            course = self.store.read(
                RevisionRef.model_validate(fact.body["course_ref"]), learner_id
            )
            material = self.store.read(
                RevisionRef.model_validate(course.body["material_refs"][0]), learner_id
            )
            if material.body.get("title"):
                titles.append(material.body["title"])
        return titles

    def calibration(self, learner_id: str) -> dict[str, float]:
        """Per task type, how long sealed classes actually took against the estimate."""
        samples = []
        for fact in reversed(latest_facts(self, learner_id)):  # Oldest first.
            course = self.store.read(
                RevisionRef.model_validate(fact.body["course_ref"]), learner_id
            )
            samples += timing_samples(fact.body, CourseBody.model_validate(course.body))
        factors = calibration(samples)
        log.info("preparation calibration types=%d samples=%d", len(factors), len(samples))
        return factors

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
        if demand.body.get("goal_confirmation") == "needed":
            raise Conflict("Confirm the new goal first; then the lesson is prepared.")
        body = DemandBody.model_validate(demand.body)
        if body.purpose != "placement" and body.topic is None:
            raise Conflict("Choose a topic for the next lesson first.")
        state, plan = [self.store.read(ref, learner_id) for ref in (body.state_ref, body.plan_ref)]
        for obj in (state, plan):
            if self.store.current(obj.object_id, learner_id).ref != obj.ref:
                raise Conflict("demand must be rechecked against current effective dependencies")
        # The student may edit the profile any time; preparation reads its latest version.
        profile = self.current(learner_id, "profile")
        if active_restrictions(self, learner_id, (demand.ref, state.ref, plan.ref, profile.ref)):
            raise Conflict("preparation dependencies are restricted; owner recheck required")
        existing = self.prepared(learner_id, demand)
        if existing is not None:  # One course per demand revision, whoever asked first.
            course = self.store.read(RevisionRef.model_validate(existing["course_ref"]), learner_id)
            if active_restrictions(self, learner_id, (course.ref, *course.dependency_refs)):
                raise Conflict(
                    "saved preparation has restricted dependencies; owner recheck required"
                )
            return existing
        operation = self.request(
            request_id,
            learner_id,
            "prepare",
            {"demand_ref": demand.ref.model_dump()},
            sources=(demand.ref,),
            baselines=(demand.ref, state.ref, plan.ref, profile.ref),
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
                publications=(publication(jane, "The one Jane method"),),
            )
        package = self.saved_step(operation, learner_id, "package")
        if package is None:
            source, familiar = self.chosen_material(learner_id, body)
            read = self.read_sources(learner_id)
            library = self.library(read)
            goals = (body.primary_goal.dimension_id, body.auxiliary_goal.dimension_id)
            # The simulated model only reproduces the library's fixed course, so it gets the
            # library; a real model writes every lesson anew from the student's interests.
            simulated = self.models.simulates("preparation")
            fits = simulated and (
                goals == (PRIMARY_GOAL.dimension_id, AUXILIARY_GOAL.dimension_id)
                or body.purpose == "placement"
            )
            offscreen = bool(profile.body.get("materials"))  # checked_course rule
            template = (
                template_draft(
                    body,
                    source,
                    library[source]["story"],
                    familiar,
                    offscreen,
                )
                if fits
                else None
            )
            request = preparation_request(
                body,
                state.body,
                profile.body,
                [doc.body for doc in self.store.list_current(learner_id, "capability")],
                jane.body,
                list(library.values()) if simulated else [],
                source if simulated else None,
                template,
                self.recent_titles(learner_id),
                lesson_score_summary(self, learner_id, "preparation"),
                level=story_level(profile.body, body.purpose == "placement"),
                vocabulary=self.offered_vocabulary(
                    self.vocabulary_record(learner_id)[1],
                    placement=body.purpose == "placement",
                    familiar=familiar,
                    today=datetime.now(UTC).date(),
                ),
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
                baselines=(demand.ref, state.ref, plan.ref, profile.ref, jane.ref),
            )
            # Courses never confirmed are replaced by this one; their drafts go (ADR-0027).
            draft = draft_of(self.store, learner_id, refs["course"].object_id)
            log.info("drafts removed count=%d", len(self.files.remove_drafts(keep=draft)))
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
                "unverified": ["actual_api", "voice"],
            },
        )

    def prepared(self, learner_id: str, demand: Document) -> dict | None:
        """The finished preparation result of a course already made for this demand."""
        for course in self.store.list_current(learner_id, "course"):
            if course.body["demand_ref"] == demand.ref.model_dump():
                return self.ledger.result(course.operation_id, learner_id)
        return None

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
            state, plan = [
                self.store.read(ref, learner_id) for ref in (body.state_ref, body.plan_ref)
            ]
            profile = self.current(learner_id, "profile")
            jane = self.store.read(RevisionRef.model_validate(course.body["jane_ref"]), learner_id)
            read = self.read_sources(learner_id)
            library = self.library(read)
            source, familiar = self.chosen_material(learner_id, body)
            base = preparation_request(
                body,
                state.body,
                profile.body,
                [doc.body for doc in self.store.list_current(learner_id, "capability")],
                jane.body,
                list(library.values()) if self.models.simulates("preparation") else [],
                source if self.models.simulates("preparation") else None,
                None,
                self.recent_titles(learner_id),
                lesson_score_summary(self, learner_id, "preparation"),
                level=story_level(profile.body, body.purpose == "placement"),
                vocabulary=self.offered_vocabulary(
                    self.vocabulary_record(learner_id)[1],
                    placement=body.purpose == "placement",
                    familiar=familiar,
                    today=datetime.now(UTC).date(),
                ),
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
                publications=publications(objects),
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
                "unverified": ["actual_api", "voice"],
            },
        )

    def confirm(self, request_id: str, learner_id: str, course_ref: RevisionRef):
        """The parent confirms one exact course version; resources then can be generated."""
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
            obj = successor(
                preview,
                operation,
                preview.body
                | {
                    "confirmation": {
                        "state": "confirmed",
                        "course_ref": course.ref.model_dump(),
                        "confirmed_at": datetime.now(UTC).isoformat(),
                    },
                },
                refs=preview.dependency_refs,
            )
            # Each resource is its own record from now on (ADR-0027).
            listed = planned_resources(course, operation)
            saved = self.save_step(
                operation,
                learner_id,
                "confirmation",
                [obj, *listed],
                publications=(
                    publication(
                        obj, "The student confirmed this lesson version; resources to be made"
                    ),
                    *(publication(item, "Listed for making") for item in listed),
                ),
                baselines=(course.ref, preview.ref),
            )
        resources = sum(item["kind"] in GENERATED for item in saved[0].body["resources"])
        # Only the confirmed version's story stays in the draft (ADR-0027).
        draft = draft_of(self.store, learner_id, course.object_id)
        removed = sum(
            self.files.remove(story_path(draft, revision)) for revision in range(1, course.revision)
        )
        log.info(
            "preparation confirmed course=%s revision=%d resources=%d stories_removed=%d",
            course.object_id,
            course.revision,
            resources,
            removed,
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
        subject = "lesson" if stage == "prepare" else "change"
        body = DemandBody.model_validate(demand.body)
        catalog = {
            doc.body["dimension_id"]
            for doc in self.store.list_current(demand.learner_id, "capability")
        }
        factors = self.calibration(demand.learner_id)
        level = request.context["story_level"]
        level = StoryLevel.model_validate(level) if level else None
        if level:
            log.info(
                "preparation story level challenge=%s target=%s words=%d-%d",
                level.challenge or "none",
                level.target,
                *level.words,
            )
        else:
            log.info("preparation story level none")
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
                    offscreen_allowed=bool(request.context["profile"].get("materials")),
                    factors=factors,
                    level=level,
                )
            except DraftRejected as rejected:
                codes = ",".join(dict.fromkeys(code for code, _ in rejected.problems))
                log.warning(
                    "preparation refused stage=%s attempt=%d checks=%s", stage, attempt, codes
                )
                reasons = [text for _, text in rejected.problems]
                if attempt == 2:
                    raise PreparationUnavailable(
                        "the rules found problems: " + "; ".join(reasons[:3]), subject
                    ) from None
                request = repair_request(request, draft, reasons)
                continue
            log.info(
                "preparation drafted stage=%s attempt=%d material=%s tasks=%d read_aloud=%d "
                "resources=%d focus=%d minutes=%d types=%d",
                stage,
                attempt,
                "library" if checked.source else "model",
                len(draft.tasks),
                len(checked.course.tasks) - len(draft.tasks),
                len(draft.resources),
                len(body.skill_focus or ()),
                sum(checked.course.budget.values()),
                len({task.purpose for task in checked.course.tasks}),
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
            limitation = (
                "Written story; no measured Lexile or RAZ level; proves no real science laws."
            )
        else:
            text = f"# {checked.title}\n\n{checked.story}\n"
            origin, source = "model", f"model:{digest(text)}"
            limitation = (
                "Story written by the model; no measured Lexile or RAZ level; only the "
                "evidence was checked against it; proves no real science laws."
            )
        material = document(
            "material",
            refs["material"].object_id,
            operation,
            None,
            {
                "status": "checked",
                "asset": self.files.put(
                    story_path(
                        draft_of(self.store, learner_id, refs["course"].object_id),
                        refs["course"].revision,
                    ),
                    text,
                ),
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
                "unverified": ["actual_api", "voice"],
            },
            refs=(course.ref, check.ref),
        )
        return [course, brief, material, check, preview]

    async def confirm_course(
        self, request_id: str, learner_id: str, course_ref: RevisionRef, on_progress=None
    ) -> dict:
        """The student confirms this version; its resources are made and adopted now, and the
        class starts only when the student chooses (confirm_and_start then reuses them)."""
        result = self.confirm(f"{request_id}:confirm", learner_id, course_ref)
        failed = await ready_resources(self, learner_id, course_ref, on_progress)
        if failed:
            raise Conflict(f"Resources not ready: {', '.join(failed)}. You can try again.")
        return result
