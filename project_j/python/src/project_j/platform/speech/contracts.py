"""Pronunciation scoring capability (ADR-0014): recording + reference text in, scores out."""

from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from project_j.contracts import Contract

Score = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class PronunciationLimits(Contract):
    max_words: int = Field(ge=1, strict=True)
    max_seconds: int = Field(ge=1, strict=True)


class WordScore(Contract):
    word: str = Field(min_length=1)
    accuracy: float = Field(ge=0, le=100, allow_inf_nan=False)
    error: Literal["none", "mispronounced", "omitted", "inserted"]


class PronunciationResult(Contract):
    """Evidence, not a grade: unknown is never filled in as a low score."""

    status: Literal["scored", "unknown"]
    reason: Literal["no_speech", "too_short", "noisy", "failed"] | None = None
    words: tuple[WordScore, ...] = ()
    accuracy: float | None = Score
    fluency: float | None = Score
    completeness: float | None = Score
    level: str | None = None  # The service's own grade label, when it gives one.
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    simulated: bool

    @model_validator(mode="after")
    def scores_match_status(self) -> Self:
        scores = (self.accuracy, self.fluency, self.completeness)
        if self.status == "scored" and (self.reason or None in scores or not self.words):
            raise ValueError("a scored result carries word scores and no failure reason")
        if self.status == "unknown" and (
            not self.reason or self.words or any(x is not None for x in scores)
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
        )


class PronunciationScorer(Protocol):
    service: str
    version: str
    simulated: bool
    limits: PronunciationLimits

    async def score(self, audio: bytes, reference_text: str) -> PronunciationResult:
        """Never raises for a bad or silent recording; returns status unknown instead."""
        ...
