import asyncio

import pytest
from pydantic import ValidationError

from project_j.platform.speech.contracts import PronunciationLimits, PronunciationResult
from project_j.platform.speech.simulated import SimulatedScorer

REFERENCE = "Mia has a toy car. She wants a bridge for her car."


def score(read: bytes):
    return asyncio.run(SimulatedScorer().score(read, REFERENCE))


def test_a_faithful_reading_scores_every_word_without_errors():
    result = score(REFERENCE.encode())
    assert result.status == "scored" and result.simulated
    assert all(word.error == "none" for word in result.words)
    assert result.completeness == 100 and result.pauses == 0 and result.limitations == ()
    assert not hasattr(result, "level") and not hasattr(result, "fluency")


def test_a_misread_word_is_marked_on_the_reference_word():
    result = score(b"Mia has a toy car. She wants a brige for her car.")
    errors = {word.word: word.error for word in result.words if word.error != "none"}
    assert errors == {"bridge": "mispronounced"}
    assert result.accuracy < 95


def test_a_skipped_word_lowers_completeness():
    result = score(b"Mia has a car. She wants a bridge for her car.")
    assert {w.word for w in result.words if w.error == "omitted"} == {"toy"}
    assert result.completeness < 100


@pytest.mark.parametrize(("audio", "reason"), [(b"", "no_speech"), (b"\xff\xfe", "failed")])
def test_no_reading_is_unknown_not_a_low_score(audio, reason):
    result = score(audio)
    assert result.status == "unknown" and result.reason == reason
    assert result.accuracy is None and not result.words


def test_scored_results_need_their_scores_and_unknown_results_carry_none():
    with pytest.raises(ValidationError):
        PronunciationResult(status="scored", service="s", version="1", simulated=True)
    with pytest.raises(ValidationError):
        PronunciationResult(
            status="unknown", reason="failed", accuracy=50, service="s", version="1", simulated=True
        )


def test_simulated_scorer_uses_product_limits_and_is_always_ready():
    from project_j.platform.speech.contracts import PRODUCT_LIMITS
    from project_j.preparation.reading import MAX_SEGMENT_WORDS

    scorer = SimulatedScorer()
    assert scorer.limits == PRODUCT_LIMITS == PronunciationLimits(max_words=100, max_seconds=300)
    assert MAX_SEGMENT_WORDS == 100 and scorer.reasons() == []


def test_unknown_results_carry_no_timing():
    with pytest.raises(ValidationError):
        PronunciationResult(
            status="unknown", reason="failed", pauses=0, service="s", version="1", simulated=False
        )
