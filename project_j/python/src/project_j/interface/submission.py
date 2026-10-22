"""In-memory input adapter: explicit typed submissions and reading recordings reach the core."""

from project_j.classroom.harness import Harness
from project_j.classroom.responses import MALFORMED
from project_j.contracts import SubmittedInput

STOP_WORDS = {"停止", "停课", "stop", "暂停", "pause"}


class InputAdapter:
    def __init__(self, core: Harness):
        self.core = core
        self.sequence = 0

    def provider(self, simulated: str) -> str:
        return "student" if self.core.realm == "study" else simulated

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

    def new_input(self, event_id: str, **fields) -> SubmittedInput:
        """An input bound to the core's current wait, with the next client sequence."""
        self.sequence += 1
        return SubmittedInput(
            **self.binding(),
            event_id=event_id,
            realm=self.core.realm,
            source_type=self.core.source_type,
            client_sequence=self.sequence,
            **({"completion_trigger": "explicit"} | fields),
        )

    def replayed(self, event_id: str, modality: str | None, **same) -> SubmittedInput | None:
        """A resent event's first body (of this modality, or of any when None); the same event
        with different content is refused."""
        original = self.core.inputs.get(event_id)
        if not original or modality not in {None, original["signature"].get("modality")}:
            return None
        old = SubmittedInput.model_validate(original["signature"])
        if any(getattr(old, field) != value for field, value in same.items()):
            raise ValueError("same input event has different content")
        return old

    async def text(self, event_id: str, text: str, intent="unknown", relation="new", related=None):
        if text.strip().casefold() in STOP_WORDS:
            command = "pause" if text.strip().casefold() in {"暂停", "pause"} else "stop"
            self.core.control(event_id, command)
            return {"ack": "accepted_control", "snapshot": self.core.snapshot()}
        content = dict(text=text, intent=intent, relation=relation, related_event_id=related)
        submitted = self.new_input(
            event_id, modality="text", provider=self.provider("simulated_speaker"), **content
        )
        # Preserve the first serialized body when a transmission is retried.
        return await self.core.submit(self.replayed(event_id, "text", **content) or submitted)

    async def response(self, event_id: str, response: dict | None, intent="answer"):
        """A clicked task's answer or hint plea; a resent one replays its first body."""
        if (response is None) != (intent == "help"):  # An answer carries one; a plea none.
            raise ValueError(MALFORMED)
        old = self.replayed(event_id, "response", response=response, intent=intent)
        return await self.core.submit(
            old
            or self.new_input(
                event_id,
                modality="response",
                response=response,
                provider=self.provider("simulated_clicker"),
                intent=intent,
            )
        )

    async def recording(
        self,
        event_id: str,
        audio: bytes,
        *,
        input_started_ms: float,
        input_ended_ms: float,
        recording_ms: float,
        pause_count: int = 0,
        completion_trigger="explicit",
    ):
        # A resent recording replays its first body; it is never scored again.
        if old := self.replayed(event_id, None):
            return await self.core.submit(old)
        submitted = self.new_input(
            event_id,
            modality="audio",
            provider=self.provider("simulated_reader"),
            intent="answer",
            completion_trigger=completion_trigger,
            input_started_ms=input_started_ms,
            input_ended_ms=input_ended_ms,
            recording_ms=recording_ms,
            pause_count=pause_count,
        )
        return await self.core.submit(submitted, audio=audio)
