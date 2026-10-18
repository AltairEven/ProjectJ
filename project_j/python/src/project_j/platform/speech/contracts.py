"""Pronunciation scoring capability (ADR-0014, ADR-0015): recording + reference text in."""

from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from project_j.contracts import Contract
from project_j.platform.storage import Conflict

Score = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class PronunciationLimits(Contract):
    max_words: int = Field(ge=1, strict=True)
    max_seconds: int = Field(ge=1, strict=True)


# Product limits bound with the runtime configuration, not taken from a service (ADR-0015).
PRODUCT_LIMITS = PronunciationLimits(max_words=100, max_seconds=300)


class WordScore(Contract):
    word: str = Field(min_length=1)
    accuracy: float = Field(ge=0, le=100, allow_inf_nan=False)
    min_phone: float | None = Score  # Lowest phoneme score, when the scorer gives one.
    error: Literal["none", "mispronounced", "omitted", "inserted"]


class PronunciationResult(Contract):
    """Evidence, not a grade: unknown is never filled in as a low score."""

    status: Literal["scored", "unknown"]
    reason: Literal["no_speech", "too_short", "noisy", "failed"] | None = None
    words: tuple[WordScore, ...] = ()
    accuracy: float | None = Score
    completeness: float | None = Score
    pauses: int | None = Field(default=None, ge=0)
    words_per_minute: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    simulated: bool
    model: str | None = None  # Model id@revision.
    thresholds: str | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def scores_match_status(self) -> Self:
        scores = (self.accuracy, self.completeness)
        if self.status == "scored" and (self.reason or None in scores or not self.words):
            raise ValueError("a scored result carries word scores and no failure reason")
        timing = (self.pauses, self.words_per_minute)
        if self.status == "unknown" and (
            not self.reason or self.words or any(x is not None for x in (*scores, *timing))
        ):
            raise ValueError("an unknown result carries a reason and no scores")
        return self

    @classmethod
    def unknown(cls, reason: str, scorer: PronunciationScorer) -> PronunciationResult:
        return cls(
            status="unknown",
            reason=reason,
            service=scorer.service,
            version=scorer.version,
            simulated=scorer.simulated,
            model=scorer.model,
            thresholds=scorer.thresholds,
            limitations=scorer.limitations,
        )


class PronunciationScorer(Protocol):
    service: str
    version: str
    simulated: bool
    limits: PronunciationLimits
    model: str | None
    thresholds: str | None
    limitations: tuple[str, ...]

    async def score(self, audio: bytes, reference_text: str) -> PronunciationResult:
        """Never raises for a bad or silent recording; returns status unknown instead."""
        ...

    def reasons(self) -> list[str]:
        """Why the scorer cannot serve a class now; empty when it can."""
        ...


class ScorerNotReady(Conflict):
    """A course with reading segments starts only with an admitted scorer (ADR-0015)."""
