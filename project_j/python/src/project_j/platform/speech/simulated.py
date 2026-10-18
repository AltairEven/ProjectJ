"""Simulated scorer: the recording bytes are the UTF-8 text the fictional child actually read."""

import re
from difflib import SequenceMatcher
from statistics import mean

from project_j.platform.speech.contracts import (
    PronunciationLimits,
    PronunciationResult,
    WordScore,
)

WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")


class SimulatedScorer:
    service = "simulated-pronunciation"
    version = "1"
    simulated = True
    # Mirrors the preferred service's paragraph mode (120 words, 300 seconds).
    limits = PronunciationLimits(max_words=120, max_seconds=300)

    async def score(self, audio: bytes, reference_text: str) -> PronunciationResult:
        try:
            heard = WORD.findall(audio.decode("utf-8"))
        except UnicodeDecodeError:
            return PronunciationResult.unknown("failed", self)
        if not heard:
            return PronunciationResult.unknown("no_speech", self)
        reference = WORD.findall(reference_text)
        matcher = SequenceMatcher(
            a=[w.casefold() for w in reference], b=[w.casefold() for w in heard], autojunk=False
        )
        words = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                words += [WordScore(word=w, accuracy=95, error="none") for w in reference[i1:i2]]
            elif tag == "replace":
                words += [
                    WordScore(word=w, accuracy=40, error="mispronounced") for w in reference[i1:i2]
                ]
            elif tag == "delete":
                words += [WordScore(word=w, accuracy=0, error="omitted") for w in reference[i1:i2]]
            else:
                words += [WordScore(word=w, accuracy=0, error="inserted") for w in heard[j1:j2]]
        read = [w for w in words if w.error != "inserted"]
        accuracy = mean(w.accuracy for w in read)
        return PronunciationResult(
            status="scored",
            words=tuple(words),
            accuracy=accuracy,
            fluency=90,
            completeness=100 * sum(w.error != "omitted" for w in read) / len(read),
            level="优" if accuracy >= 85 else "良" if accuracy >= 60 else "待加强",
            service=self.service,
            version=self.version,
            simulated=True,
        )
