"""In-memory simulated recognizer adapter; fragments never enter the answer path."""

import time
from dataclasses import dataclass, field
from uuid import uuid4

from project_j.classroom.harness import Harness
from project_j.contracts import SubmittedInput


@dataclass
class Segment:
    item_id: str
    event_id: str
    binding: dict
    text: str = ""
    candidate_end_ms: float | None = None
    final: bool = False
    window: bool = False
    cancelled: bool = False
    submitted: bool = False
    source: str = "simulated_speaker"
    revisions: list[str] = field(default_factory=list)
    server_candidate_clock: float | None = None


class InputAdapter:
    def __init__(self, core: Harness, on_revision=None):
        self.core = core
        self.on_revision = on_revision
        self.segments: dict[str, Segment] = {}
        self.sequence = 0

    def binding(self):
        state = self.core.snapshot()
        return {
            "activity_id": state["activity_id"],
            "learner_id": state["learner_id"],
            "task_instance_id": state["task_instance_id"],
            "wait_id": state["wait_id"],
            "expected_core_revision": state["core_revision"],
            "cancellation_epoch": state["cancellation_epoch"],
            "input_policy_ref": state["input_policy_ref"],
        }

    async def text(self, event_id: str, text: str, intent="unknown", relation="new", related=None):
        if text.strip().casefold() in {"停止", "停课", "stop", "暂停", "pause"}:
            command = "pause" if text.strip().casefold() in {"暂停", "pause"} else "stop"
            self.core.control(event_id, command)
            return {"ack": "accepted_control", "snapshot": self.core.snapshot()}
        self.sequence += 1
        submitted = SubmittedInput(
            **self.binding(),
            event_id=event_id,
            realm="simulation",
            source_type="simulated",
            client_sequence=self.sequence,
            modality="text",
            text=text,
            provider="simulated_speaker",
            intent=intent,
            completion_trigger="explicit",
            relation=relation,
            related_event_id=related,
        )
        # Preserve the first serialized body when a transmission is retried.
        original = self.core.inputs.get(event_id)
        if original and original["signature"].get("modality") == "text":
            old = SubmittedInput.model_validate(original["signature"])
            if (
                old.text != text
                or old.intent != intent
                or old.relation != relation
                or old.related_event_id != related
            ):
                raise ValueError("same input event has different content")
            submitted = old
        return await self.core.submit(submitted)

    async def voice_event(
        self, kind: str, item_id: str, text="", candidate_end_ms=None, source="simulated_speaker"
    ):
        if kind == "start":
            if self.core.mode in {"paused", "offscreen", "ended"}:
                raise ValueError("recognition is suspended at the current boundary")
            if item_id in self.segments:
                raise ValueError("recognition item identity already exists")
            self.segments[item_id] = Segment(item_id, str(uuid4()), self.binding(), source=source)
        segment = self.segments.get(item_id)
        if segment is None:
            raise ValueError("recognition signal has no original segment")
        self.core.record(
            "simulated_recognition",
            {
                "kind": kind,
                "item_id": item_id,
                "text": text,
                "submitted": segment.submitted,
                "source": segment.source,
            },
        )
        if source == "teacher_echo" or segment.source == "teacher_echo":
            segment.cancelled = True
        elif kind == "fragment":
            segment.text = text
        elif kind == "candidate_end":
            if candidate_end_ms is None or candidate_end_ms < 0:
                raise ValueError("candidate end needs the client monotonic timestamp")
            segment.candidate_end_ms = candidate_end_ms
            segment.server_candidate_clock = time.monotonic()
        elif kind == "resume":
            if segment.submitted:
                raise ValueError("a new utterance after submission needs its own input identity")
            segment.window = False
            segment.candidate_end_ms = None
            segment.final = False
        elif kind == "final":
            segment.text, segment.final = text, True
            if text.strip().casefold() in {"停止", "停课", "stop", "暂停", "pause"}:
                segment.cancelled = True
                return await self.text(segment.event_id, text)
        elif kind == "window_elapsed":
            if segment.candidate_end_ms is None:
                raise ValueError("pause window cannot elapse before a candidate end")
            segment.window = True
        elif kind == "cancel":
            segment.cancelled = True
        elif kind == "revision":
            if not segment.submitted:
                segment.text, segment.final = text, True
            else:
                self.sequence += 1
                revised = SubmittedInput(
                    **segment.binding,
                    event_id=str(uuid4()),
                    realm="simulation",
                    source_type="simulated",
                    client_sequence=self.sequence,
                    modality="transcript",
                    provider="simulated_recognizer_revision",
                    intent="unknown",
                    text=text,
                    recognition_final=True,
                    recognition_item_ids=(item_id,),
                    completion_trigger="explicit",
                    relation="recognition_revision",
                    related_event_id=segment.event_id,
                )
                segment.revisions.append(revised.event_id)
                result = await self.core.submit(revised)
                if self.on_revision:
                    self.on_revision(revised)
                return result
        elif kind != "start":
            raise ValueError("unknown recognition signal")
        if (
            not segment.cancelled
            and not segment.submitted
            and segment.final
            and segment.window
            and segment.candidate_end_ms is not None
        ):
            self.sequence += 1
            submitted = SubmittedInput(
                **segment.binding,
                event_id=segment.event_id,
                realm="simulation",
                source_type="simulated",
                client_sequence=self.sequence,
                modality="transcript",
                text=segment.text,
                provider=segment.source,
                intent="unknown",
                recognition_final=True,
                recognition_item_ids=(item_id,),
                completion_trigger="vad_window",
                candidate_end_ms=segment.candidate_end_ms,
                window_elapsed=True,
            )
            result = await self.core.submit(
                submitted, deadline=(segment.server_candidate_clock or time.monotonic()) + 10
            )
            segment.submitted = result["ack"] in {"accepted", "replayed"}
            return result
        return {
            "ack": "cancelled" if segment.cancelled else "waiting",
            "snapshot": self.core.snapshot(),
        }
