import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_j.preparation.packages import student_text
from project_j.preparation.reading import MAX_SEGMENT_WORDS, segment_reading, words

ROOT = Path(__file__).resolve().parents[3]


def story(name):
    text = student_text((ROOT / "courses/paper-bridge" / name).read_text())
    return text.split("\n", 1)[1]


def test_a_long_story_splits_at_sentence_ends_under_the_limit():
    text = story("reading-main.md")
    segments = segment_reading(text)
    assert len(segments) == 2 and len(words(text)) > MAX_SEGMENT_WORDS
    assert all(len(words(segment)) <= MAX_SEGMENT_WORDS for segment in segments)
    assert " ".join(segments) == " ".join(text.split())  # Nothing dropped or reordered.
    # A quoted question and its speaker stay one sentence.
    assert any("“Can I make a new bridge?” says Mia." in segment for segment in segments)


def test_a_short_story_is_one_segment():
    assert len(segment_reading(story("reading-transfer.md"))) == 1


@pytest.mark.parametrize(
    "text", ["word " * (MAX_SEGMENT_WORDS + 1) + ".", "No ending punctuation", ""]
)
def test_unsplittable_or_unpunctuated_text_is_rejected(text):
    with pytest.raises(ValueError):
        segment_reading(text)


def test_a_read_aloud_task_needs_a_short_punctuated_reference():
    from project_j.preparation.contracts import Support, TaskDefinition

    base = dict(
        task_id="r",
        dimension_id="english.speaking.pronunciation",
        purpose="read_aloud",
        prompt="读",
        material_ref={"object_id": "M", "revision": 1},
        evidence="以评分为据",
        acceptable_simulated=(),
        reading_id="story",
        supports=(
            Support(text="a", help={"english.speaking.pronunciation": "light_prompt"}),
            Support(text="b", help={"english.speaking.pronunciation": "full_model"}),
        ),
        exit_text="跳过",
    )
    assert TaskDefinition(**base, reference_text="Mia has a toy car.").reference_text
    for bad in (None, "word " * (MAX_SEGMENT_WORDS + 1) + ".", "no punctuation"):
        with pytest.raises(ValidationError):
            TaskDefinition(**base | {"reference_text": bad})
    with pytest.raises(ValidationError):
        TaskDefinition(**base | {"purpose": "answer", "reference_text": "Mia has a car."})


def test_prepared_course_ends_with_read_aloud_segments(application):
    prepared = asyncio.run(application.prepare("prepare-reading", "fictional-L1"))
    from project_j.contracts import RevisionRef

    course = application.store.read(
        RevisionRef.model_validate(prepared["course_ref"]), "fictional-L1"
    )
    tasks = course.body["tasks"]
    reading = [task for task in tasks if task["purpose"] == "read_aloud"]
    assert [task["task_id"] for task in reading] == ["read-aloud-1", "read-aloud-2"]
    assert tasks[-len(reading) - 1]["task_id"] == "paper-experiment"
    assert all(task["dimension_id"] == "english.speaking.pronunciation" for task in reading)
