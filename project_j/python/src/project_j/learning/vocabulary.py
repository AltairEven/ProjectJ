"""The learner's own words: marked while reading, weighted for the next lesson, retired by
rule once they keep appearing unmarked (design/learning/profile.md)."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from project_j.contracts import Contract
from project_j.preparation.reading import words

EXIT_THRESHOLD = 5  # Met unmarked this many times: mastered.
CANDIDATE_LIMIT = 100  # Transport cap for preparation, not a usage cap.
Status = Literal["active", "mastered"]


class WordMark(Contract):
    course_title: str
    sentence: str
    date: str  # ISO date


class LookedUp(Contract):
    """One marked word after the lookup; a failed lookup has no lemma."""

    word: str
    sentence: str
    lemma: str | None = None
    part_of_speech: str | None = None
    definition: str | None = None
    audio: dict | None = None  # binary_asset manifest


class WordEntry(Contract):
    lemma: str
    forms: tuple[str, ...]
    part_of_speech: str | None = None
    definition: str | None = None
    audio: dict | None = None
    status: Status = "active"
    mark_count: int = Field(ge=1)
    exit_count: int = 0
    first_marked: str
    last_marked: str
    last_seen: str
    # Mastered: last_seen is the day, unmarked_appearances[-1] the story; the class is kept
    # for the evaluation page.
    mastered_activity: str | None = None
    marks: tuple[WordMark, ...] = Field(min_length=1)
    unmarked_appearances: tuple[str, ...] = ()


class VocabularyBody(Contract):
    status: Literal["checked"] = "checked"
    words: tuple[WordEntry, ...] = ()

    @model_validator(mode="after")
    def unique_lemmas(self) -> Self:
        if len({entry.lemma for entry in self.words}) != len(self.words):
            raise ValueError("each lemma has one entry")
        return self


def tokens(text: str) -> set[str]:
    return {word.lower() for word in words(text)}


def weight(entry: WordEntry, today: date) -> int:
    return entry.mark_count + (today - date.fromisoformat(entry.last_seen)).days // 7


def update_after_lesson(
    body: VocabularyBody,
    marked: tuple[LookedUp, ...],
    story_text: str | None,
    course_title: str,
    today: date,
    activity_id: str | None = None,
) -> tuple[VocabularyBody, dict]:
    """Marked words gain a mark and lose their exits; active words met unmarked in the finished
    story gain an exit (story_text None: the story was not finished, nothing is met)."""
    day = today.isoformat()
    by_lemma: dict[str, list[LookedUp]] = {}
    for item in marked:
        by_lemma.setdefault(item.lemma or item.word.lower(), []).append(item)
    entries = {entry.lemma: entry for entry in body.words}
    counts = {"new": 0, "marked": 0, "exits": 0, "mastered": 0}
    for lemma, items in by_lemma.items():
        forms = tuple(dict.fromkeys(item.word.lower() for item in items))
        marks = tuple(  # One mark per sentence, however many forms were tapped in it.
            WordMark(course_title=course_title, sentence=sentence, date=day)
            for sentence in dict.fromkeys(i.sentence for i in items)
        )
        found = next((item for item in items if item.definition), None)
        counts["marked"] += 1
        if (entry := entries.get(lemma)) is None:
            counts["new"] += 1
            entries[lemma] = WordEntry(
                lemma=lemma,
                forms=forms,
                part_of_speech=found.part_of_speech if found else None,
                definition=found.definition if found else None,
                audio=found.audio if found else None,
                mark_count=1,
                first_marked=day,
                last_marked=day,
                last_seen=day,
                marks=marks,
            )
            continue
        entries[lemma] = entry.model_copy(
            update={
                "forms": tuple(dict.fromkeys((*entry.forms, *forms))),
                "part_of_speech": entry.part_of_speech or (found.part_of_speech if found else None),
                "definition": entry.definition or (found.definition if found else None),
                "audio": entry.audio or (found.audio if found else None),
                "status": "active",
                "mark_count": entry.mark_count + 1,
                "exit_count": 0,
                "last_marked": day,
                "last_seen": day,
                "mastered_activity": None,
                "marks": entry.marks + marks,
            }
        )
    met = tokens(story_text) if story_text is not None else set()
    for lemma, entry in entries.items():
        if lemma in by_lemma or entry.status != "active":
            continue
        if lemma not in met and not met.intersection(entry.forms):
            continue
        counts["exits"] += 1
        exits = entry.exit_count + 1
        mastered = exits >= EXIT_THRESHOLD
        counts["mastered"] += mastered
        entries[lemma] = entry.model_copy(
            update={
                "exit_count": exits,
                "last_seen": day,
                "unmarked_appearances": (*entry.unmarked_appearances, course_title),
                "status": "mastered" if mastered else "active",
                "mastered_activity": activity_id if mastered else None,
            }
        )
    return body.model_copy(update={"words": tuple(entries.values())}), counts


def _active(body: VocabularyBody, today: date) -> list[WordEntry]:
    active = (e for e in body.words if e.status == "active")
    return sorted(active, key=lambda e: (-weight(e, today), e.lemma))


def candidates(body: VocabularyBody, today: date) -> list[dict]:
    """Active words for preparation, heaviest first, at most CANDIDATE_LIMIT."""
    return [
        {
            "lemma": entry.lemma,
            "part_of_speech": entry.part_of_speech,
            "weight": weight(entry, today),
            "mark_count": entry.mark_count,
            "days_since_seen": (today - date.fromisoformat(entry.last_seen)).days,
        }
        for entry in _active(body, today)[:CANDIDATE_LIMIT]
    ]


def listing(body: VocabularyBody, today: date) -> list[dict]:
    """Every word for the vocabulary page: active by weight, then mastered newest first."""
    mastered = (e for e in body.words if e.status == "mastered")
    newest = sorted(mastered, key=lambda e: (e.last_seen, e.lemma), reverse=True)
    return [
        entry.model_dump(mode="json") | {"weight": weight(entry, today)}
        for entry in (*_active(body, today), *newest)
    ]
