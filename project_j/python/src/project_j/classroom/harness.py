"""Explicit state/action boundary. Providers propose; only this core can advance."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from project_j.classroom.judgment import ModelProposal, judgment_request, method_context
from project_j.classroom.responses import describe, judge, option_pictures, presentation
from project_j.contracts import Contract, RevisionRef, SubmittedInput
from project_j.english import chinese
from project_j.learning.vocabulary import LookedUp
from project_j.platform.models.service import ModelService
from project_j.platform.speech.contracts import PronunciationResult, PronunciationScorer
from project_j.platform.storage import REALMS, Conflict, canonical, digest
from project_j.preparation.contracts import CourseBody, ResourceSpec, TaskDefinition
from project_j.preparation.task_types import PROGRAM_JUDGED
from project_j.preparation.voice import WRONG_TEXT, opening_text, question_speech, question_text

log = logging.getLogger(__name__)
# Diagnostic logs carry identities, kinds and outcomes only, never what was said or shown.
LOGGED_FIELDS = (
    "event_id", "action_id", "task_id", "task_instance_id", "dimension_id", "purpose", "kind",
    "after_delivery", "status", "modality", "intent", "relation", "completion_trigger",
    "recording_ms", "delivered_range", "failure", "deadline_seconds", "judgment", "call_id",
    "command", "reason", "unconfirmed", "unfinished_tasks", "elapsed_seconds", "type_version",
    "wrong_count", "rule_ms", "level", "reading_ms", "word_count", "first_reading", "pauses",
    "marked",
)  # fmt: skip
# Reading the story before the questions (ADR-0022): what typing, help or skip gets meanwhile.
READING_WAITS = {
    "reading_start": 'Press "Start reading" to see the story.',
    "reading": 'Press "I\'ve finished reading" when you are done.',
}
TRANSITIONS = {
    "delivering": {"waiting_input", "delivering", "offscreen", "paused", "ended"},
    "waiting_input": {"judging", "delivering", "paused", "ended"},
    "judging": {"delivering", "waiting_input", "paused", "ended"},
    "offscreen": {"delivering", "paused", "ended"},
    "paused": {"delivering", "waiting_input", "offscreen", "ended"},
    "ended": set(),
}
# A bare plea: nothing is left once help words, fillers and punctuation are removed.
PLEA = re.compile(
    r"\b(?:help|please|need|can|could|you|me|i|still|don['’]?t|do|not|understand|the|question)\b"
    r"|[\W_]+"
)
# Asking for help in English (ADR-0021); "I don't know" is an unclear answer, not a plea.
HELP = re.compile(
    r"\bhelp\b|\bi\s+(?:still\s+)?(?:don['’]?t|do\s+not)\s+understand\b", re.IGNORECASE
)
CONFIRM = re.compile(r"\b(?:yes|yeah|ok|okay|got it|sure|understood|i heard)\b", re.IGNORECASE)


def is_help_request(task: TaskDefinition, text: str, intent: str) -> bool:
    """The core, not the model, decides whether an input asks for help."""
    return intent == "help" or (
        # A report task asks what help was used, so there only a bare plea is a help request.
        not PLEA.sub("", text.casefold())
        if task.purpose in {"point", "offscreen"}
        else bool(HELP.search(text))
    )


def is_confirmation(text: str) -> bool:
    """The student says they heard the plan or the summary."""
    return bool(CONFIRM.search(text))


# Per realm: a text attempt, a recorded attempt, the sealed activity.
LIMITATIONS = {
    "simulation": (
        "Simulated; outside help, recall and transfer are unknown.",
        "Simulated recording and score; not real pronunciation.",
        "Simulated activity, not a learning record.",
    ),
    "study": (
        "Real study; outside help, recall and transfer are unknown.",
        "Your reading was scored on this computer; the score limits are not calibrated.",
        "Real study activity; recall and transfer are unknown.",
    ),
}
NOT_ENGLISH = "model proposal text is not in English"
HELP_ORDER = {"none": 0, "light_prompt": 1, "scaffold": 2, "full_model": 3, "unknown": 4}
SCORE_RULE_NOTE = "No judgment came in time; this reply follows fixed rules from the score."


def score_rule(pronunciation: dict | None) -> tuple[str, str]:
    """A reading's prepared response when no judgment came: the scorer's own word flags."""
    if not pronunciation or pronunciation["status"] != "scored":
        return "unclear", (
            "No reading came through, so it is not counted as wrong. "
            "Press the microphone to read again, or skip."
        )
    flagged = dict.fromkeys(
        word["word"].strip(".,!?;:\"'")
        for word in pronunciation["words"]
        if word["error"] in {"mispronounced", "omitted"}
    )
    if not flagged:
        return "correct", "You read this part. Let's go on."
    return "incorrect", f"Look again at these words: {', '.join(list(flagged)[:3])}."


class AllowedAction(Contract):
    action_id: str
    activity_id: str
    task_instance_id: str
    wait_id: str
    core_revision: int
    cancellation_epoch: int
    kind: Literal[
        "goal", "question", "feedback", "help", "clarification", "offscreen", "closing", "repeat"
    ]
    text: str = Field(min_length=1)
    audience: Literal["student"] = "student"
    material_refs: tuple[RevisionRef, ...] = ()
    help: dict[str, str] = {}
    # What Jane says, part by part (empty: the whole text); adopted recordings play as is.
    speech: tuple[str, ...] = ()
    # Adopted pictures or animations shown with this action, by resource id.
    visuals: tuple[str, ...] = ()
    after_delivery: Literal[
        "input", "retry", "clarification", "next_task", "offscreen", "end_confirmation"
    ]


class DeliveryFeedback(Contract):
    delivery_event_id: str
    delivery_id: str
    action_id: str
    activity_id: str
    task_instance_id: str
    sequence: int = Field(ge=1, strict=True)
    output_device_id: str
    # image: which of the action's pictures or animations actually displayed (resource ids).
    modality: Literal["text", "simulated_audio", "audio", "image"]
    status: Literal[
        "started", "partial", "completed", "failed", "cancelled", "interrupted", "unknown"
    ]
    delivered_range: tuple[str, ...]
    occurred_at: AwareDatetime


class Harness:
    def __init__(
        self,
        activity_id: str,
        learner_id: str,
        course_ref: RevisionRef,
        course: CourseBody,
        bindings: dict,
        models: ModelService,
        scorer: PronunciationScorer | None = None,
        *,
        realm: Literal["simulation", "study"] = "simulation",
        jane: dict | None = None,
        voiced: frozenset[str] = frozenset(),
        visible: tuple[ResourceSpec, ...] = (),
        story_words: int = 0,
        first_reading: bool = True,
    ):
        self.activity_id, self.learner_id = activity_id, learner_id
        # Texts with an adopted recording; adopted student pictures and animations.
        self.voiced, self.visible = voiced, visible
        self.jane = jane  # The Jane version the course is bound to.
        self.realm, self.source_type = realm, REALMS[realm][1]
        self.course_ref, self.course, self.bindings, self.models = (
            course_ref,
            course,
            bindings,
            models,
        )
        self.scorer = scorer
        self.mode = "delivering"
        self.wait_kind = "reading_start"
        # The story shows once the student starts reading; the timer runs on the core clock
        # until "finished", excluding pauses (ms so far, running since, pause count).
        self.story_words, self.first_reading = story_words, first_reading
        self.story_open = False
        self.story_timer: dict | None = None
        # Words marked while reading, by lowercase surface form; filled in after reading.
        self.box: dict[str, dict] = {}
        self.revision, self.epoch, self.task_index = 1, 0, -1
        self.task_instance_id, self.wait_id = str(uuid4()), str(uuid4())
        self.started_at, self.started_clock = datetime.now(UTC), time.monotonic()
        self.events: list[dict] = []
        self.attempts: list[dict] = []
        self.inputs: dict[str, dict] = {}
        self.actions: dict[str, AllowedAction] = {}
        self.pending: str | None = None
        self.delivery_sequences: dict[str, int] = {}
        self.delivery_events: dict[str, str] = {}
        self.delivered: set[str] = set()
        # The last action the student fully saw, so a reopened page can show it again.
        self.presented: str | None = None
        self.help_by_task: dict[str, dict] = {}
        # Pictures and animations the page reported as displayed, by task instance.
        self.seen: dict[str, dict[str, str]] = {}
        self.help_count: dict[str, int] = {}
        self.wrong_count: dict[str, int] = {}
        self.jobs: set[asyncio.Task] = set()
        self.warming: set[asyncio.Task] = set()
        self.paused_from: str | None = None
        self.end: dict | None = None
        self.last_response_event: str | None = None
        self.closing_clock: float | None = None
        self.task_started_ms: float | None = None
        # A review lesson must not present an already-read story as a first reading.
        self.action("goal", opening_text(self.course), "input", visuals=self.shown((), "question"))

    def record(self, kind: str, payload: dict):
        self.events.append(
            {
                "sequence": len(self.events) + 1,
                "kind": kind,
                "received_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        )
        fields = " ".join(f"{key}={payload[key]}" for key in LOGGED_FIELDS if key in payload)
        log.info("event %s activity=%s seq=%d %s", kind, self.activity_id, len(self.events), fields)

    def move(self, mode: str):
        if mode not in TRANSITIONS[self.mode]:
            raise ValueError(f"disallowed classroom transition {self.mode}->{mode}")
        self.mode = mode
        self.revision += 1

    def invalidate(self):
        self.epoch += 1
        self.pending = None
        for job in tuple(self.jobs):
            job.cancel()

    def shown(self, task_ids: tuple[str, ...], stage: str) -> tuple[str, ...]:
        """Adopted visuals for these tasks at this stage; ones tied to no task open the class."""
        return tuple(
            item.resource_id
            for item in self.visible
            if item.stage == stage
            and (set(item.task_ids) & set(task_ids) if task_ids else not item.task_ids)
        )

    def action(
        self,
        kind: str,
        text: str,
        after: str,
        help_map=None,
        materials=(),
        speech=(),
        visuals=(),
    ):
        if self.mode in {"ended", "paused"}:
            raise ValueError("action cannot be issued after stop or while paused")
        if self.mode != "delivering":
            self.move("delivering")
        else:
            self.revision += 1
        action = AllowedAction(
            action_id=str(uuid4()),
            activity_id=self.activity_id,
            task_instance_id=self.task_instance_id,
            wait_id=self.wait_id,
            core_revision=self.revision,
            cancellation_epoch=self.epoch,
            kind=kind,
            text=text,
            after_delivery=after,
            help=help_map or {},
            material_refs=materials,
            speech=speech,
            visuals=visuals,
        )
        self.actions[action.action_id] = action
        self.pending = action.action_id
        self.record("allowed_action", action.model_dump(mode="json"))
        return action

    def story_visible(self) -> bool:
        """The story shows after "Start reading", except while reading is paused."""
        return self.story_open and not (self.mode == "paused" and self.story_timer is not None)

    def illustrations(self) -> list[dict]:
        """Adopted story pictures by paragraph; the page shows them beside the story."""
        story = [item for item in self.visible if item.stage == "story"]
        return [
            {"resource_id": item.resource_id, "paragraph": item.paragraph}
            for item in sorted(story, key=lambda item: item.paragraph)
        ]

    def option_ids(self) -> list[str]:
        """The current clicked task's option pictures, when all of them are adopted."""
        clicked = self.clicked_task()
        return (option_pictures(clicked, self.visible) or []) if clicked else []

    def picture_available(self, resource_id: str) -> bool:
        """A story picture while the story shows, or an option picture of the current task."""
        story = {item["resource_id"] for item in self.illustrations()}
        return (self.story_visible() and resource_id in story) or resource_id in self.option_ids()

    def pictures_allowed(self, action: AllowedAction) -> set[str]:
        """What a picture report on this action may name: its own visuals, the story pictures
        while the story shows, and its task's option pictures."""
        allowed = set(action.visuals)
        if self.story_visible():
            allowed |= {item["resource_id"] for item in self.illustrations()}
        if action.task_instance_id == self.task_instance_id:
            allowed |= set(self.option_ids())
        return allowed

    def stop_story(self, kind: str):
        """Record how long the student read (reading_finished or reading_unfinished)."""
        timer = self.story_timer
        running = self.class_ms() - timer["since"] if timer["since"] is not None else 0.0
        self.story_timer = None
        self.record(
            kind,
            {
                "reading_ms": round(timer["ms"] + running),
                "word_count": self.story_words,
                "first_reading": self.first_reading,
                "pauses": timer["pauses"],
            },
        )

    def mark_word(self, event_id: str, word: str, sentence: str, marked: bool):
        """Mark or unmark a story word; only while the story is being read (not paused)."""
        signature = {"event_id": event_id, "word": word, "sentence": sentence, "marked": marked}
        previous = self.inputs.get(event_id)
        if previous:
            if previous["signature"] != signature:
                raise Conflict("same mark identity has different content")
            return self.snapshot()
        if self.story_timer is None or self.mode not in {"waiting_input", "delivering"}:
            raise ValueError("Mark words while you read the story.")
        self.inputs[event_id] = {"signature": signature, "attempt_id": None}
        key = word.lower()
        if marked:
            self.box.setdefault(
                key,
                {
                    "word": word,
                    "sentence": sentence,
                    "status": "marked",
                    "lemma": None,
                    "part_of_speech": None,
                    "definition": None,
                    "audio": False,
                },
            )
        else:
            self.box.pop(key, None)
        self.revision += 1
        self.record("word_marked" if marked else "word_unmarked", signature)
        return self.snapshot()

    def pending_words(self) -> list[dict]:
        """Marked words still without a meaning: what the lookup after reading asks for."""
        return [
            {"word": value["word"], "sentence": value["sentence"]}
            for value in self.box.values()
            if value["status"] in {"marked", "pending"}
        ]

    def set_word(self, surface: str, looked: LookedUp | None, audio: bool):
        """The lookup's outcome for one marked word; None means it failed (retry possible)."""
        entry = self.box[surface]
        if looked is None:
            entry["status"] = "failed"
        else:
            entry.update(
                status="ready",
                lemma=looked.lemma,
                part_of_speech=looked.part_of_speech,
                definition=looked.definition,
                audio=audio,
            )
        self.revision += 1
        self.record("word_lookup", {"word": surface, "status": entry["status"]})

    def current_task(self) -> TaskDefinition | None:
        if self.mode == "ended" or not 0 <= self.task_index < len(self.course.tasks):
            return None
        return self.course.tasks[self.task_index]

    def reading_task(self) -> TaskDefinition | None:
        task = self.current_task()
        return task if task and task.purpose == "read_aloud" else None

    def clicked_task(self) -> TaskDefinition | None:
        """The current task when it is answered on the page by clicking or filling."""
        task = self.current_task()
        return task if task and task.purpose in PROGRAM_JUDGED else None

    def task_view(self) -> dict | None:
        """The clicked task as the page shows it, with its option pictures when all exist."""
        clicked = self.clicked_task()
        if clicked is None:
            return None
        pictures = self.option_ids()
        return presentation(clicked) | ({"pictures": pictures} if pictures else {})

    def class_ms(self) -> float:
        """Milliseconds since the class started, for the attempt record's time points."""
        return round((time.monotonic() - self.started_clock) * 1000, 1)

    def snapshot(self):
        action = self.actions.get(self.pending) if self.pending else None
        activity_state = (
            "closing"
            if self.wait_kind == "closing_confirmation" and self.mode not in {"ended", "paused"}
            else self.mode
            if self.mode in {"offscreen", "paused", "ended"}
            else "running"
        )
        return {
            "activity_id": self.activity_id,
            "learner_id": self.learner_id,
            "realm": self.realm,
            "source_type": self.source_type,
            "mode": self.mode,
            "activity_state": activity_state,
            "wait_kind": self.wait_kind,
            "task_instance_id": self.task_instance_id,
            "wait_id": self.wait_id,
            "core_revision": self.revision,
            "cancellation_epoch": self.epoch,
            "input_policy_ref": self.bindings["input_policy"],
            "pending_action": action.model_dump(mode="json") if action else None,
            "presented_action": self.actions[self.presented].model_dump(mode="json")
            if self.presented
            else None,
            "attempt_count": len(self.attempts),
            # Which task of how many; -1 while the goal is being confirmed.
            "task_index": self.task_index,
            "task_count": len(self.course.tasks),
            "elapsed_seconds": time.monotonic() - self.started_clock,
            "recording": {
                "max_seconds": self.scorer.limits.max_seconds,
                "reference_text": reading.reference_text,
            }
            if (reading := self.reading_task()) and self.scorer
            else None,
            # A clicked task as the page shows it; never its answer.
            "task": self.task_view(),
            "vocabulary": [value | {"surface": key} for key, value in self.box.items()],
            "end": self.end,
            "actual_api": "unverified",
            "actual_voice": "unverified",
        }

    def delivery(self, feedback: DeliveryFeedback):
        if feedback.activity_id != self.activity_id:
            raise ValueError("wrong delivery activity")
        fingerprint = digest(canonical(feedback))
        if feedback.delivery_event_id in self.delivery_events:
            if self.delivery_events[feedback.delivery_event_id] != fingerprint:
                raise Conflict("same delivery event has different content")
            return self.snapshot()
        action = self.actions.get(feedback.action_id)
        if action is None or action.task_instance_id != feedback.task_instance_id:
            raise ValueError("delivery must refer to an approved action and its task")
        if feedback.modality == "image" and not set(
            feedback.delivered_range
        ) <= self.pictures_allowed(action):
            raise ValueError("a picture report may name only the visuals shown with this action")
        previous = self.delivery_sequences.get(feedback.delivery_id, 0)
        if feedback.sequence <= previous:
            raise Conflict("out-of-order delivery feedback")
        self.delivery_sequences[feedback.delivery_id] = feedback.sequence
        self.delivery_events[feedback.delivery_event_id] = fingerprint
        self.record("delivery", feedback.model_dump(mode="json"))
        if feedback.modality == "image":  # A fact for evaluation; the speech or text moves on.
            log.info(
                "class pictures activity=%s part=%s shown=%d status=%s",
                self.activity_id,
                feedback.delivery_id.rsplit(":", 1)[-1],
                len(feedback.delivered_range),
                feedback.status,
            )
            stages = {item.resource_id: item.stage for item in self.visible}
            seen = self.seen.setdefault(action.task_instance_id, {})
            seen.update({resource: stages[resource] for resource in feedback.delivered_range})
            return self.snapshot()
        if feedback.delivered_range and feedback.status in {
            "started",
            "partial",
            "completed",
            "interrupted",
        }:
            self.delivered.add(action.action_id)
            levels = self.help_by_task.setdefault(action.task_instance_id, {})
            for dimension, level in action.help.items():
                if HELP_ORDER[level] > HELP_ORDER[levels.get(dimension, "none")]:
                    levels[dimension] = level
        if (
            self.mode in {"ended", "paused"}
            or self.pending != action.action_id
            or action.cancellation_epoch != self.epoch
        ):
            return self.snapshot()
        if feedback.status in {"failed", "cancelled", "interrupted", "unknown"}:
            self.pending = None
            self.move("waiting_input")
            # A missed repeat while reading leaves the student reading, not recovering.
            self.wait_kind = "reading" if self.story_timer is not None else "delivery_recovery"
            return self.snapshot()
        if feedback.status != "completed" or not feedback.delivered_range:
            return self.snapshot()
        self.pending = None
        self.presented = action.action_id
        self.move("waiting_input")
        if action.after_delivery == "next_task":
            self.next_task()
        elif action.after_delivery == "offscreen":
            self.move("delivering")
            self.move("offscreen")
            self.wait_kind = "offscreen_return"
        elif action.after_delivery == "end_confirmation":
            self.wait_kind = "closing_confirmation"
        else:
            before = "reading" if self.story_timer is not None else "reading_start"
            self.wait_kind = {"retry": "retry", "clarification": "clarification"}.get(
                action.after_delivery, before if self.task_index < 0 else "answer"
            )
            if self.reading_task() and hasattr(self.scorer, "warm"):
                # After synthesis the scorer's first score took 8–28 s under memory pressure,
                # past the 10-second deadline; paging it in while the student reads avoids that.
                warm = asyncio.create_task(self.scorer.warm())
                self.warming.add(warm)
                warm.add_done_callback(self.warming.discard)
        return self.snapshot()

    def next_task(self):
        self.task_index += 1
        self.wait_id, self.task_instance_id = str(uuid4()), str(uuid4())
        # Thirty minutes estimate the lesson; the class still finishes its tasks (ADR-0020).
        if self.task_index >= len(self.course.tasks):
            self.wait_kind = "closing_confirmation"
            return self.action(
                "closing",
                "We keep your real answers, the help you had and what was not seen. Next class "
                "follows from today. Did you hear this summary?",
                "end_confirmation",
            )
        task, budget = self.course.tasks[self.task_index], self.course.budget
        self.wait_kind = "answer"
        self.task_started_ms = self.class_ms()
        self.record(
            "task_started",
            {
                "task_id": task.task_id,
                "task_instance_id": self.task_instance_id,
                "dimension_id": task.dimension_id,
                "purpose": task.purpose,
                "type_version": task.type_version,
            },
        )
        text, spoken = question_text(task, budget), question_speech(task, budget)
        visuals = self.shown((task.task_id,), "question")
        if task.purpose == "offscreen":
            return self.action(
                "offscreen", text, "offscreen", materials=(task.material_ref,), visuals=visuals
            )
        return self.action(
            "question",
            text,
            "input",
            materials=(task.material_ref,),
            speech=() if spoken == text else (spoken,),
            visuals=visuals,
        )

    def control(self, event_id: str, command: str):
        signature = {"event_id": event_id, "command": command}
        previous = self.inputs.get(event_id)
        if previous:
            if previous["signature"] != signature:
                raise Conflict("same control identity has different content")
            return self.snapshot()
        self.inputs[event_id] = {"signature": signature, "attempt_id": None}
        self.record("control", signature)
        if command == "stop":
            if self.mode != "ended":
                self.finish("explicit_stop")
        elif command == "pause":
            if self.mode not in {"paused", "ended"}:
                self.paused_from = self.mode
                timer = self.story_timer
                if timer is not None and timer["since"] is not None:
                    timer["ms"] += self.class_ms() - timer["since"]
                    timer["since"], timer["pauses"] = None, timer["pauses"] + 1
                self.invalidate()
                self.move("paused")
        elif command == "resume":
            if self.mode != "paused":
                raise ValueError("only the original paused activity can resume")
            previous_mode = self.paused_from
            if self.story_timer is not None:  # Back to reading; the opening is not said again.
                self.move("waiting_input")
                self.story_timer["since"] = self.class_ms()
                return self.snapshot()
            self.move("offscreen" if previous_mode == "offscreen" else "delivering")
            if previous_mode != "offscreen":
                last = next(reversed(self.actions.values()))
                self.action(
                    "repeat",
                    last.text,
                    last.after_delivery,
                    last.help,
                    last.material_refs,
                    last.speech,
                    last.visuals,
                )
        elif command == "return":
            if self.mode != "offscreen":
                raise ValueError("return does not create an offscreen result")
            self.move("delivering")
            self.wait_kind = "offscreen_report"
            self.action(
                "question",
                "Tell me the things you used, what you changed, what happened and any help. "
                'You can say "I don\'t know" for what you did not see.',
                "input",
            )
        elif command == "skip":
            if self.task_index < 0:  # No question yet: skipping would bypass the story.
                raise ValueError(
                    READING_WAITS.get(self.wait_kind, 'Press "Say it again" to hear Jane first.')
                )
            # Skipping is the way out of a slow judgment or an offscreen task, not only of a wait.
            if self.mode in {"waiting_input", "delivering", "judging", "offscreen"}:
                self.invalidate()
                self.record("task_skipped", {"task_instance_id": self.task_instance_id})
                self.next_task()
        elif command == "repeat":
            if self.mode in {"waiting_input", "delivering"}:
                last = next(reversed(self.actions.values()))
                self.action(
                    "repeat",
                    last.text,
                    last.after_delivery,
                    last.help,
                    last.material_refs,
                    last.speech,
                    last.visuals,
                )
        elif command == "close":
            if self.mode not in {"ended", "paused"}:
                self.invalidate()
                if self.story_timer is not None:
                    self.stop_story("reading_unfinished")
                self.wait_kind = "closing_confirmation"
                self.action(
                    "closing",
                    "We sum up only what happened; the rest stays unknown. "
                    "Did you hear this summary?",
                    "end_confirmation",
                )
        elif command == "start_reading":
            if self.mode != "waiting_input" or self.wait_kind != "reading_start":
                raise ValueError("Start reading only after Jane's opening.")
            self.story_open = True
            self.story_timer = {"ms": 0.0, "since": self.class_ms(), "pauses": 0}
            self.wait_kind = "reading"
            self.revision += 1
            self.record(
                "reading_started",
                {"word_count": self.story_words, "first_reading": self.first_reading},
            )
        elif command == "finish_reading":
            if self.story_timer is None or self.mode not in {"waiting_input", "delivering"}:
                raise ValueError("Nothing is being read now.")
            self.invalidate()
            self.stop_story("reading_finished")
            self.next_task()
        elif command == "end_unconfirmed":
            if self.mode != "ended":
                self.finish("unconfirmed")
        else:
            raise ValueError("unknown control")
        return self.snapshot()

    def finish(self, reason: str):
        if self.story_timer is not None:  # Recorded before the end boundary is fixed.
            self.stop_story("reading_unfinished")
        self.invalidate()
        self.move("ended")
        self.end = {
            "end_id": str(uuid4()),
            "reason": {"explicit_stop": "stop", "normal_confirmed": "normal"}.get(reason, reason),
            "ended_at": datetime.now(UTC).isoformat(),
            "last_valid_event": len(self.events),
            "core_revision": self.revision,
            "unconfirmed": reason != "normal_confirmed",
            "unfinished_tasks": [
                task.task_id for task in self.course.tasks[max(self.task_index, 0) :]
            ],
        }
        self.record("end", self.end)

    async def submit(
        self,
        submitted: SubmittedInput,
        deadline: float | None = None,
        audio: bytes | None = None,
    ):
        if submitted.activity_id != self.activity_id or submitted.learner_id != self.learner_id:
            raise ValueError("input activity or learner mismatch")
        if submitted.realm != self.realm or submitted.source_type != self.source_type:
            raise ValueError("input realm or source does not match this activity")
        if submitted.input_policy_ref != RevisionRef.model_validate(self.bindings["input_policy"]):
            raise ValueError("input policy does not match the fixed activity binding")
        signature = submitted.model_dump(mode="json")
        previous = self.inputs.get(submitted.event_id)
        if previous:
            if previous["signature"] != signature:
                raise Conflict("same input identity has different content")
            return {"ack": "replayed", "event_id": submitted.event_id, "snapshot": self.snapshot()}
        if self.wait_kind in READING_WAITS:
            raise ValueError(READING_WAITS[self.wait_kind])
        if submitted.intent == "confirmation" and self.wait_kind != "closing_confirmation":
            raise ValueError("Nothing to confirm now. Please answer the current question.")
        supplement = submitted.relation == "supplement"
        if supplement:
            original = self.inputs.get(submitted.related_event_id)
            if (
                original is None
                or original["signature"].get("task_instance_id") != self.task_instance_id
            ):
                raise ValueError("supplement needs the original current-task input")
            # After the response was delivered the core rules the utterance new under the current
            # wait (a retry becomes a new attempt); it never joins or washes the first answer.
            supplement = original.get("response_action") not in self.delivered
            if supplement:
                self.invalidate()
                if self.mode in {"judging", "delivering"}:
                    self.move("waiting_input")
        if not supplement and (
            submitted.task_instance_id != self.task_instance_id
            or submitted.wait_id != self.wait_id
            or submitted.expected_core_revision != self.revision
            or submitted.cancellation_epoch != self.epoch
        ):
            raise Conflict("stale input binding")
        if self.mode != "waiting_input":
            raise ValueError("core is not waiting for a completed input")
        reading = self.reading_task()
        answering = self.wait_kind in {"answer", "retry", "clarification"}
        clicked = self.clicked_task() if answering else None
        if submitted.modality == "response":
            if clicked is None:
                raise ValueError("This question is not answered with buttons.")
            return self.submit_response(clicked, submitted, signature)
        if clicked is not None:
            raise ValueError('Answer on the question itself, or press "Hint".')
        if submitted.modality == "audio":
            if reading is None or audio is None or not answering or self.scorer is None:
                raise ValueError("Recordings are only for the current reading part.")
            if submitted.recording_ms > self.scorer.limits.max_seconds * 1000:
                raise ValueError("The recording is longer than this part allows.")
        elif reading is not None and answering and submitted.intent != "help":
            if not HELP.search(submitted.text):
                raise ValueError("For a reading part, press the microphone and read aloud.")
        self.record("input", signature)
        self.inputs[submitted.event_id] = {"signature": signature, "attempt_id": None}
        if submitted.relation == "supplement" and not supplement:
            self.record(
                "relation_ruled",
                {
                    "event_id": submitted.event_id,
                    "relation": "new",
                    "reason": "original response delivered",
                },
            )
        if self.wait_kind == "closing_confirmation":
            if submitted.intent == "confirmation" or is_confirmation(submitted.text):
                self.record("closing_received", {"event_id": submitted.event_id})
                self.finish("normal_confirmed")
            else:
                self.action(
                    "clarification",
                    "Did you hear the summary? You can also just end the class without saying so.",
                    "end_confirmation",
                )
            return {"ack": "accepted", "snapshot": self.snapshot()}
        if self.wait_kind == "delivery_recovery":
            raise ValueError("repeat or stop after a failed delivery; do not rescore the answer")
        task = self.course.tasks[self.task_index]
        text = submitted.text or ""
        is_help = is_help_request(task, text, submitted.intent)
        attempt = None
        if not is_help:
            if supplement:
                original_id = self.inputs[submitted.related_event_id]["attempt_id"]
                attempt = next((a for a in self.attempts if a["attempt_id"] == original_id), None)
            if attempt is None:
                attempt = self.new_attempt(task, submitted.modality == "audio")
                self.attempts.append(attempt)
            attempt["inputs"].append(signature)
            self.inputs[submitted.event_id]["attempt_id"] = attempt["attempt_id"]
        self.move("judging")
        binding = dict(
            activity_id=self.activity_id,
            task_instance_id=self.task_instance_id,
            wait_id=self.wait_id,
            core_revision=self.revision,
            cancellation_epoch=self.epoch,
            event_id=submitted.event_id,
        )
        # The configured answer deadline, read now so a change applies to the next submit.
        allowed = self.models.deadline("classroom_judgment")
        timeout = max(0, (deadline or time.monotonic() + allowed) - time.monotonic())
        help_count = self.help_count.get(self.task_instance_id, 0)

        async def judge():
            pronunciation = None
            if audio is not None:  # The recording lives only in this call, never in a record.
                scoring = time.perf_counter()
                try:
                    pronunciation = await self.scorer.score(audio, task.reference_text)
                except Exception:  # A scorer must not raise; treat it as an unknown score.
                    pronunciation = PronunciationResult.unknown("failed", self.scorer)
                # Counts against the configured answer deadline, so it is kept with the attempt.
                scoring_ms = round((time.perf_counter() - scoring) * 1000, 1)
                log.info(
                    "pronunciation task=%s status=%s scoring_ms=%.0f reason=%s",
                    task.task_id,
                    pronunciation.status,
                    scoring_ms,
                    pronunciation.reason,
                )
                if self.epoch != binding["cancellation_epoch"]:
                    raise asyncio.CancelledError  # Stopped meanwhile: drop the late score.
                if attempt is not None:
                    attempt["pronunciation"] = pronunciation.model_dump(mode="json")
                    attempt["scoring_ms"] = scoring_ms
            return await self.models.run(
                judgment_request(
                    task,
                    text,
                    help_count,
                    is_help,
                    timeout,
                    pronunciation,
                    jane_method=method_context(self.jane),
                    goals=(
                        self.course.primary_goal.description,
                        self.course.auxiliary_goal.description,
                    ),
                )
            )

        job = asyncio.create_task(judge())
        self.jobs.add(job)
        try:
            result = await asyncio.wait_for(job, timeout=timeout)
        except asyncio.CancelledError:
            return {"ack": "cancelled", "snapshot": self.snapshot()}
        except TimeoutError:
            result = None
        finally:
            self.jobs.discard(job)
        if result is None or not result.ok:
            if self.mode == "judging" and self.epoch == binding["cancellation_epoch"]:
                self.record(
                    "model_failure",
                    {
                        "event_id": submitted.event_id,
                        "deadline_seconds": round(timeout, 3),
                        "failure": result.failure if result else "timeout",
                    },
                )
                if task.purpose == "read_aloud" and attempt is not None:
                    # Scoring plus a judgment often passes 10 s; the score's own flags answer.
                    judgment, text = score_rule(attempt.get("pronunciation"))
                    attempt["limitations"].append(SCORE_RULE_NOTE)
                    self.record(
                        "judgment_fallback",
                        {
                            "event_id": submitted.event_id,
                            "task_instance_id": self.task_instance_id,
                            "judgment": judgment,
                        },
                    )
                    return self.respond(
                        task, submitted.event_id, attempt, judgment, task.evidence, text, True
                    )
                self.move("waiting_input")
                self.action(
                    "clarification",
                    "I could not check that answer. It is kept: add to it, skip, or stop.",
                    "clarification",
                )
            return {"ack": "failed", "snapshot": self.snapshot()}
        proposal = ModelProposal(**binding, **result.output.model_dump(), call_id=result.call_id)
        try:
            self.approve(proposal)
        except ValueError as error:
            if self.mode == "judging" and self.epoch == binding["cancellation_epoch"]:
                reason = (
                    "binding"
                    if isinstance(error, Conflict)
                    else "language"
                    if str(error) == NOT_ENGLISH
                    else "evidence"
                )
                log.info("classroom proposal rejected reason=%s", reason)
                self.record("proposal_rejected", {"event_id": submitted.event_id, "reason": reason})
                self.move("waiting_input")
                self.action(
                    "clarification",
                    "I could not use that reply. Your answer is kept: say more, skip, or stop.",
                    "clarification",
                )
            return {"ack": "rejected_proposal", "snapshot": self.snapshot()}
        return self.respond(
            task, submitted.event_id, attempt, proposal.judgment, proposal.evidence, proposal.text
        )

    def new_attempt(self, task: TaskDefinition, recorded: bool) -> dict:
        """A new try at the current task, with the help and pictures it had and when it came
        (class milliseconds; judged_ms is set when it is judged)."""
        looked = {item.resource_id for item in self.visible if item.picture_question}
        return {
            "attempt_id": str(uuid4()),
            "task_instance_id": self.task_instance_id,
            "task_id": task.task_id,
            "dimension_id": task.dimension_id,
            "purpose": task.purpose,
            "inputs": [],
            "help": dict(self.help_by_task.get(self.task_instance_id, {})),
            "visuals": [
                {"resource_id": resource, "stage": stage}
                | ({"picture_question": True} if resource in looked else {})
                for resource, stage in self.seen.get(self.task_instance_id, {}).items()
            ],
            "external_help": "unknown",
            "judgment": "unclear",
            "limitations": [LIMITATIONS[self.realm][recorded]],
            "type_version": task.type_version,
            "task_started_ms": self.task_started_ms,
            "answered_ms": self.class_ms(),
            "judged_ms": None,
        }

    def submit_response(self, task: TaskDefinition, submitted: SubmittedInput, signature: dict):
        """A clicked task: judged here by its own rule, no model; a hint takes the next help."""
        event_id = submitted.event_id
        if submitted.intent == "help":
            self.record("input", signature)
            self.inputs[event_id] = {"signature": signature, "attempt_id": None}
            level = self.help_count.get(self.task_instance_id, 0) + 1
            self.record(
                "hint",
                {
                    "task_instance_id": self.task_instance_id,
                    "purpose": task.purpose,
                    "level": level,
                },
            )
            return self.respond(task, event_id, None, "help", task.evidence, "", by_rule=True)
        started = time.perf_counter()
        correct = judge(task, submitted.response)  # A malformed answer is refused, not counted.
        rule_ms = round((time.perf_counter() - started) * 1000, 2)
        self.record("input", signature)
        self.inputs[event_id] = {"signature": signature, "attempt_id": None}
        attempt = self.new_attempt(task, recorded=False) | {
            "inputs": [signature],
            "response": submitted.response,
            "answer_text": describe(task, submitted.response),
        }
        self.attempts.append(attempt)
        self.inputs[event_id]["attempt_id"] = attempt["attempt_id"]
        judgment = "correct" if correct else "incorrect"
        self.record(
            "rule_judged",
            {
                "event_id": event_id,
                "task_instance_id": self.task_instance_id,
                "purpose": task.purpose,
                "judgment": judgment,
                "wrong_count": self.wrong_count.get(self.task_instance_id, 0) + (not correct),
                "rule_ms": rule_ms,
            },
        )
        text = task.correct_text if correct else WRONG_TEXT
        return self.respond(task, event_id, attempt, judgment, task.evidence, text, by_rule=True)

    def respond(self, task, event_id, attempt, judgment, evidence, text, by_rule=False):
        if attempt:
            attempt["judgment"] = judgment
            attempt["evidence"] = evidence
            attempt["judged_ms"] = self.class_ms()
        # Each wrong answer and each plea gets the next support of its own ladder; past the last
        # one the task exits instead of asking for yet another try.
        help_map, ladder = {}, None
        if judgment == "incorrect":
            ladder = self.wrong_count
        elif judgment == "help":
            ladder = self.help_count
        if ladder is not None:
            count = ladder.get(self.task_instance_id, 0)
            ladder[self.task_instance_id] = count + 1
            if count >= len(task.supports):
                self.record(
                    "task_exited",
                    {"task_instance_id": self.task_instance_id, "reason": "supports_used"},
                )
                action = self.action("feedback", task.exit_text, "next_task")
                self.inputs[event_id]["response_action"] = action.action_id
                return {"ack": "accepted", "snapshot": self.snapshot()}
            help_map = task.supports[count].help
            if by_rule:  # No model wording: the support is said as prepared.
                text = f"{text} {task.supports[count].text}".lstrip()
        speech = ()
        # The full-model help of a reading segment plays its adopted demonstration.
        if (
            task.purpose == "read_aloud"
            and "full_model" in help_map.values()
            and task.reference_text in self.voiced
        ):
            speech = (text, task.reference_text)
        action = self.action(
            "help" if help_map else "feedback",
            text,
            "next_task"
            if judgment in {"correct", "reported"}
            else "clarification"
            if judgment == "unclear"
            else "retry",
            help_map,
            speech=speech,
            visuals=self.shown((task.task_id,), "help") if help_map else (),
        )
        self.inputs[event_id]["response_action"] = action.action_id
        return {"ack": "accepted", "snapshot": self.snapshot()}

    def approve(self, proposal: ModelProposal):
        if (
            self.mode != "judging"
            or proposal.activity_id != self.activity_id
            or proposal.task_instance_id != self.task_instance_id
            or proposal.wait_id != self.wait_id
            or proposal.core_revision != self.revision
            or proposal.cancellation_epoch != self.epoch
            or proposal.event_id not in self.inputs
        ):
            raise Conflict("expired or out-of-bound model proposal")
        if proposal.evidence != self.course.tasks[self.task_index].evidence:
            raise ValueError("model proposal does not use the fixed task evidence")
        if chinese(proposal.text):  # The student hears English only (ADR-0021).
            raise ValueError(NOT_ENGLISH)
        self.record("approved_proposal", proposal.model_dump(mode="json"))

    def facts(self):
        if self.mode != "ended" or self.end is None:
            raise ValueError("facts can only be sealed after the fixed end boundary")
        return {
            "status": "checked",
            "activity_id": self.activity_id,
            "started_at": self.started_at.isoformat(),
            "course_ref": self.course_ref.model_dump(),
            "bindings": self.bindings,
            "end": self.end,
            "events": [
                event
                for event in self.events
                if event["sequence"] <= self.end["last_valid_event"] + 1
            ],
            "attempts": self.attempts,
            "qualification": "not_granted",
            "limitations": [LIMITATIONS[self.realm][2]],
        }
