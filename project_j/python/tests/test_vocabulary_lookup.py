"""Word meanings: a fifth model purpose, one request per box, rule-checked definitions."""

import asyncio

from project_j.learning.lookup import (
    WordDefinition,
    WordDefinitions,
    accepted,
    definition_problem,
    lookup_request,
)
from project_j.platform.models.contracts import PURPOSE_LABELS, PURPOSES, REQUIRED, admission
from project_j.platform.models.service import ModelService


def definition(**over):
    base = {
        "word": "folds",
        "lemma": "fold",
        "part_of_speech": "verb",
        "definition": "to bend something so one part lies on another",
    }
    return WordDefinition(**(base | over))


def test_vocabulary_is_a_registered_purpose():
    assert "vocabulary" in PURPOSES and PURPOSE_LABELS["vocabulary"] == "Word meanings"
    assert REQUIRED["vocabulary"] == ("auth", "model", "structured_output")


def test_request_shape_and_deadline():
    request = lookup_request([{"word": "folds", "sentence": "Mia folds the paper."}])
    assert (request.purpose, request.deadline_seconds, request.output) == (
        "vocabulary",
        60,
        WordDefinitions,
    )
    assert request.context == {"words": [{"word": "folds", "sentence": "Mia folds the paper."}]}


def test_definition_problems():
    assert definition_problem(definition()) is None
    assert definition_problem(definition(definition="把纸弯起来")) == "language"
    assert definition_problem(definition(definition=" ".join(["word"] * 21))) == "length"
    assert definition_problem(definition(lemma="Fold")) == "lemma"
    assert definition_problem(definition(definition="when you fold a paper")) == "circular"
    assert definition_problem(definition(definition="Folds are bends")) == "circular"


def test_accepted_matches_by_word_and_drops_bad_or_missing_ones():
    requested = [
        {"word": "folds", "sentence": "s"},
        {"word": "Stays", "sentence": "s"},
        {"word": "car", "sentence": "s"},
    ]
    reply = WordDefinitions(
        words=(definition(), definition(word="stays", lemma="stay", definition="你好"))
    )
    found = accepted(reply, requested)
    assert found["folds"].lemma == "fold" and found["stays"] is None and found["car"] is None
    assert accepted(None, requested) == {"folds": None, "stays": None, "car": None}


def test_simulated_adapter_answers_every_word_with_an_acceptable_definition():
    models = ModelService.simulated()
    request = lookup_request(
        [{"word": "folds", "sentence": "s"}, {"word": "Mia’s", "sentence": "s"}]
    )
    reply = asyncio.run(models.run(request))
    assert reply.ok and [item.lemma for item in reply.output.words] == ["folds", "mia’s"]
    assert all(definition_problem(item) is None for item in reply.output.words)
    assert admission(models.reports[0], "vocabulary", "low") == []
