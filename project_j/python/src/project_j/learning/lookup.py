"""Meanings of the words a student marked: one model request per box, checked by rule."""

import re

from pydantic import BaseModel

from project_j.english import chinese
from project_j.platform.models.contracts import ModelRequest
from project_j.preparation.reading import words

DEFINITION_WORDS = 20
LEMMA = re.compile(r"^[a-z]+(?:['’][a-z]+)?$")
VOCABULARY_INSTRUCTIONS = (
    "words是学生读英文故事时标记的生词，sentence是它在故事里所在的句子（用来区分词义）。"
    "对每个词返回：word原样照抄；lemma是小写原形（如folds→fold，Mia’s→mia’s）；part_of_speech词性；"
    "definition一条全英文释义，不超过20个英文单词，用比故事更简单的词或一句短话解释该词在这句话里的意思，"
    "不能出现这个词本身或它的原形，不写中文，不复述故事情节。"
)


class WordDefinition(BaseModel):
    word: str
    lemma: str
    part_of_speech: str
    definition: str


class WordDefinitions(BaseModel):
    words: tuple[WordDefinition, ...]


def lookup_request(requested: list[dict]) -> ModelRequest:
    """requested: [{"word", "sentence"}]; the context carries no learner identity."""
    return ModelRequest(
        "vocabulary",
        VOCABULARY_INSTRUCTIONS,
        {"words": requested},
        WordDefinitions,
        deadline_key="vocabulary",
    )


def definition_problem(item: WordDefinition) -> str | None:
    """Why a definition cannot be shown: language, length, lemma form or circularity."""
    if chinese(item.definition):
        return "language"
    if len(words(item.definition)) > DEFINITION_WORDS:
        return "length"
    if not LEMMA.match(item.lemma):
        return "lemma"
    said = {word.lower() for word in words(item.definition)}
    if {item.word.lower(), item.lemma} & said:
        return "circular"
    return None


def accepted(
    reply: WordDefinitions | None, requested: list[dict]
) -> dict[str, WordDefinition | None]:
    """Each requested word (lowercased) to its usable definition, or None when missing,
    duplicated or faulty."""
    found: dict[str, WordDefinition | None] = {}
    for item in reply.words if reply else ():
        key = item.word.lower()
        if key in found:
            found[key] = None
        elif definition_problem(item) is None:
            found[key] = item
        else:
            found[key] = None
    return {item["word"].lower(): found.get(item["word"].lower()) for item in requested}
