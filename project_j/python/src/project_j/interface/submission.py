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

    async def text(self, event_id: str, text: str, intent="unknown", relation="new", related=None):
        if text.strip().casefold() in STOP_WORDS:
            command = "pause" if text.strip().casefold() in {"暂停", "pause"} else "stop"
            self.core.control(event_id, command)
            return {"ack": "accepted_control", "snapshot": self.core.snapshot()}
        self.sequence += 1
        submitted = SubmittedInput(
            **self.binding(),
            event_id=event_id,
            realm=self.core.realm,
            source_type=self.core.source_type,
            client_sequence=self.sequence,
            modality="text",
            text=text,
            provider=self.provider("simulated_speaker"),
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

    async def response(self, event_id: str, response: dict | None, intent="answer"):
        """A clicked task's answer or hint plea; a resent one replays its first body."""
        if (response is None) != (intent == "help"):  # An answer carries one; a plea none.
            raise ValueError(MALFORMED)
        original = self.core.inputs.get(event_id)
        if original and original["signature"].get("modality") == "response":
            old = SubmittedInput.model_validate(original["signature"])
            if old.response != response or old.intent != intent:
                raise ValueError("same input event has different content")
            return await self.core.submit(old)
        self.sequence += 1
        submitted = SubmittedInput(
            **self.binding(),
            event_id=event_id,
            realm=self.core.realm,
            source_type=self.core.source_type,
            client_sequence=self.sequence,
            modality="response",
            response=response,
            provider=self.provider("simulated_clicker"),
            intent=intent,
            completion_trigger="explicit",
        )
        return await self.core.submit(submitted)

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
        original = self.core.inputs.get(event_id)
        if original:  # A resent recording replays its first body; it is never scored again.
            return await self.core.submit(SubmittedInput.model_validate(original["signature"]))
        self.sequence += 1
        submitted = SubmittedInput(
            **self.binding(),
            event_id=event_id,
            realm=self.core.realm,
            source_type=self.core.source_type,
            client_sequence=self.sequence,
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
