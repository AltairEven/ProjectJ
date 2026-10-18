"""Speech synthesis capability (ADR-0014, ADR-0016): approved text in, streamed audio out."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from project_j.platform.storage import Conflict

FailureReason = Literal["not_ready", "empty_text", "too_long", "failed", "timeout", "truncated"]


class SynthesisFailed(Exception):
    def __init__(self, reason: FailureReason):
        super().__init__(reason)
        self.reason = reason


class Synthesizer(Protocol):
    service: str
    version: str
    simulated: bool
    model: str | None  # Model id@revision.
    voice: str  # Versioned voice configuration.
    sample_rate: int

    def stream(self, text: str, *, seed: int | None = None) -> AsyncIterator[bytes]:
        """Mono 16-bit little-endian PCM chunks of core-approved text.

        Close it (contextlib.aclosing) to cancel. Raises SynthesisFailed; the chunks already
        yielded are everything the caller actually received.
        """
        ...

    def reasons(self) -> list[str]:
        """Why the synthesizer cannot serve a class now; empty when it can."""
        ...


class SynthesizerNotReady(Conflict):
    """A class delivering Jane by voice starts only with an admitted synthesizer (ADR-0016)."""
