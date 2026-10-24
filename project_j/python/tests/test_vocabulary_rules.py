"""Words the student marks while reading: one learner record, rule-updated after each lesson."""

from datetime import date

from project_j.learning.vocabulary import (
    LookedUp,
    VocabularyBody,
    candidates,
    listing,
    tokens,
    update_after_lesson,
    weight,
)

D0, D1 = date(2026, 10, 24), date(2026, 11, 14)
STORY = "Mia folds the paper. She folds it again. Mia’s car stays."


def looked(word, lemma=None, sentence="Mia folds the paper."):
    return LookedUp(
        word=word,
        sentence=sentence,
        lemma=lemma or word.lower(),
        part_of_speech="verb",
        definition="to bend something",
        audio=None,
    )


def test_tokens_keep_apostrophes_and_lowercase():
    assert tokens(STORY) == {
        "mia", "folds", "the", "paper", "she", "it", "again", "mia’s", "car", "stays",
    }  # fmt: skip


def test_marking_creates_an_active_entry_and_merges_forms_by_lemma():
    body, counts = update_after_lesson(
        VocabularyBody(), (looked("fold"), looked("folds", "fold")), STORY, "Paper", D0
    )
    assert counts == {"new": 1, "marked": 1, "exits": 0, "mastered": 0}
    (entry,) = body.words
    assert (entry.lemma, set(entry.forms), entry.mark_count, entry.exit_count) == (
        "fold",
        {"fold", "folds"},
        1,
        0,
    )
    assert entry.first_marked == entry.last_marked == entry.last_seen == "2026-10-24"
    assert [mark.sentence for mark in entry.marks] == ["Mia folds the paper."]


def test_appearing_unmarked_five_times_masters_and_remarking_revives():
    body, _ = update_after_lesson(VocabularyBody(), (looked("folds"),), STORY, "Paper", D0)
    for n in range(5):
        body, counts = update_after_lesson(body, (), STORY, f"Lesson {n}", D1, f"act-{n}")
    entry = body.words[0]
    # Mastered on its last appearance: last_seen is the day, unmarked_appearances[-1] the story.
    assert (entry.status, entry.exit_count, entry.last_seen, entry.mastered_activity) == (
        "mastered",
        5,
        "2026-11-14",
        "act-4",
    )
    assert counts["mastered"] == 1 and entry.unmarked_appearances[-1] == "Lesson 4"
    assert len(entry.unmarked_appearances) == 5
    body, _ = update_after_lesson(body, (looked("folds"),), STORY, "Again", D1)
    revived = body.words[0]
    assert (revived.status, revived.exit_count, revived.mark_count, revived.mastered_activity) == (
        "active",
        0,
        2,
        None,
    )


def test_unfinished_reading_counts_no_exits_and_other_words_are_untouched():
    body, _ = update_after_lesson(VocabularyBody(), (looked("folds"),), STORY, "Paper", D0)
    body, counts = update_after_lesson(body, (), None, "Stopped", D1)
    assert counts["exits"] == 0 and body.words[0].exit_count == 0
    body, counts = update_after_lesson(body, (), "No such words here.", "Other", D1)
    assert counts["exits"] == 0 and body.words[0].last_seen == "2026-10-24"


def test_weight_adds_marks_and_whole_weeks_since_seen():
    body, _ = update_after_lesson(VocabularyBody(), (looked("folds"),), STORY, "Paper", D0)
    assert weight(body.words[0], D0) == 1 and weight(body.words[0], D1) == 4  # 21 days


def test_candidates_sort_by_weight_skip_mastered_and_cap_at_100():
    body = VocabularyBody()
    for n in range(102):
        body, _ = update_after_lesson(body, (looked(f"w{n}"),), f"w{n}.", "L", D0)
    last = body.words[-1].model_copy(update={"status": "mastered"})
    body = body.model_copy(update={"words": (*body.words[:-1], last)})
    body, _ = update_after_lesson(body, (looked("w5"),), "w5.", "L2", D0)
    found = candidates(body, D1)
    assert len(found) == 100 and found[0]["lemma"] == "w5" and found[0]["weight"] == 5
    assert found[0]["mark_count"] == 2 and found[0]["days_since_seen"] == 21
    assert all(item["lemma"] != "w101" for item in found)


def test_listing_puts_active_by_weight_then_mastered_newest_first():
    body, _ = update_after_lesson(VocabularyBody(), (looked("a"), looked("b")), "a b.", "L", D0)
    D2 = date(2026, 10, 30)  # six days later: no whole week yet
    body, _ = update_after_lesson(body, (looked("b"),), "b.", "L2", D2)
    rows = listing(body, D2)
    assert [row["lemma"] for row in rows] == ["b", "a"]
    assert [row["weight"] for row in rows] == [2, 1]
    mastered = body.words[0].model_copy(update={"status": "mastered"})
    body = body.model_copy(update={"words": (mastered, body.words[1])})
    assert [row["lemma"] for row in listing(body, D2)] == ["b", "a"]
