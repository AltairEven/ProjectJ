"""Split a reading into scoring segments at sentence ends (ADR-0014, course package)."""

import re
from math import ceil

from project_j.platform.speech.contracts import PRODUCT_LIMITS

MAX_SEGMENT_WORDS = PRODUCT_LIMITS.max_words  # Product limit (ADR-0015).
WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
SENTENCE = re.compile(r"[^.!?]+[.!?]+[”\"’]?")


def flat(text: str) -> str:
    """Line breaks and runs of spaces are one space: a quote may join lines of a story."""
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return WORD.findall(text)


def sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    if not flat or SENTENCE.sub("", flat).strip():
        raise ValueError("every sentence of a reading must end with punctuation")
    found: list[str] = []
    for piece in (match.strip() for match in SENTENCE.findall(flat)):
        if found and piece[:1].islower():  # “…?” says Mia. is one sentence.
            found[-1] += " " + piece
        else:
            found.append(piece)
    return found


def segment_reading(text: str, limit: int = MAX_SEGMENT_WORDS) -> tuple[str, ...]:
    parts = sentences(text)
    counts = [len(words(part)) for part in parts]
    if max(counts) > limit:
        raise ValueError("a sentence longer than one scoring segment needs manual splitting")
    total = sum(counts)
    # The fewest near-equal segments that all fit; each sentence alone always fits.
    for count in range(ceil(total / limit), len(parts) + 1):
        target, segments, current, size = total / count, [], [], 0
        for part, part_words in zip(parts, counts, strict=True):
            current.append(part)
            size += part_words
            if size >= target:
                segments.append(" ".join(current))
                current, size = [], 0
        if current:
            segments.append(" ".join(current))
        if all(len(words(segment)) <= limit for segment in segments):
            return tuple(segments)
    return tuple(parts)
