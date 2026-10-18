import pytest
from helpers import PAUSE, VOCAB, WORDS, posteriors, speak

from project_j_scorer.score import score_posteriors
from project_j_scorer.text import RefWord

PHONES = [w.phones for w in WORDS]


def run(*items):
    return score_posteriors(posteriors(speak(*items)), VOCAB, WORDS)


def errors(result):
    return {w["word"]: w["error"] for w in result["words"] if w["error"] != "none"}


def test_faithful_reading_has_no_errors():
    result = run(*PHONES)
    assert result["status"] == "scored" and errors(result) == {}
    assert result["accuracy"] > 90 and result["completeness"] == 100 and result["pauses"] == 0


def test_misread_content_word_is_marked_with_a_low_lowest_phone():
    result = run(*PHONES[:4], ("k", "æ"))
    assert errors(result) == {"car": "mispronounced"}
    assert next(w for w in result["words"] if w["word"] == "car")["min_phone"] < 5


def test_function_word_is_never_marked_misread():
    assert errors(run(PHONES[0], PHONES[1], ("æ",), *PHONES[3:])) == {}


def test_group_variants_score_as_correct():
    result = run(("m", "iː", "ɐ"), PHONES[1], ("ə",), *PHONES[3:])
    assert errors(result) == {} and result["accuracy"] > 90


def test_skipped_word_is_omitted_and_lowers_completeness():
    result = run(*PHONES[:3], PHONES[4])
    assert errors(result) == {"toy": "omitted"} and result["completeness"] == 80


def test_a_weak_phone_swallowed_by_blank_is_not_omitted():
    frames = speak(*PHONES)
    frames = [f + "?" if f == "ɐ" else f for f in frames]
    result = score_posteriors(posteriors(frames), VOCAB, WORDS)
    assert errors(result) == {} and result["completeness"] == 100


def test_a_faint_phone_the_alignment_still_finds_is_not_omitted():
    frames = [f + "~" if f == "ɐ" else f for f in speak(*PHONES)]
    result = score_posteriors(posteriors(frames), VOCAB, WORDS)
    assert errors(result) == {} and result["completeness"] == 100


def test_a_diphthong_heard_as_its_parts_is_not_misread():
    words = [RefWord("Mia", ("m", "iə")), RefWord("car", ("k", "ɑːɹ"))]
    frames = speak(("m", "iː", "ɚ"), ("k", "ɑːɹ"))
    assert errors(score_posteriors(posteriors(frames), VOCAB, words)) == {}


def test_extra_phones_become_one_inserted_entry_after_the_word_before_them():
    result = run(*PHONES[:3], ("k", "iː", "m", "iː"), *PHONES[3:])
    assert [w["word"] for w in result["words"]] == ["Mia", "has", "a", "k iː m iː", "toy", "car"]
    assert errors(result) == {"k iː m iː": "inserted"} and result["completeness"] == 100


def test_a_long_gap_counts_one_pause():
    assert run(*PHONES[:2], PAUSE, *PHONES[2:])["pauses"] == 1


def test_words_per_minute_spans_first_to_last_word():
    # Words start at frame 5 and the last phone ends at frame 46.
    assert run(*PHONES)["words_per_minute"] == pytest.approx(5 / (41 * 0.02 / 60), abs=1)


def test_silence_is_no_speech_and_too_few_frames_is_too_short():
    assert score_posteriors(posteriors(["<pad>"] * 50), VOCAB, WORDS) == {
        "status": "unknown",
        "reason": "no_speech",
    }
    assert score_posteriors(posteriors(["m", "iː", "ə", "h"]), VOCAB, WORDS) == {
        "status": "unknown",
        "reason": "too_short",
    }
