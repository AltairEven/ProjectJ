"""Reference text to expected phones; whole sentences keep weak forms such as a → ɐ."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from project_j_scorer.align import edit_ops
from project_j_scorer.rules import same

WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")  # Same as the main app's preparation/reading.py.

# Sentences in, per sentence a list of words, each a list of phones.
Phonemize = Callable[[list[str]], list[list[list[str]]]]


@dataclass(frozen=True)
class RefWord:
    text: str
    phones: tuple[str, ...]


def words(text: str) -> list[str]:
    return WORD.findall(text)


def reference_phones(text: str, phonemize: Phonemize) -> list[RefWord]:
    """Sentence forms aligned back onto each word's own phones.

    espeak joins some function words ("on the") and adds linking sounds ("paper on" → ...ɹ);
    aligning the whole sentence's phones to the per-word phones keeps weak forms, drops linking
    sounds, and lets a word the sentence lost fall back to its own phones.
    """
    tokens = words(text)
    if not tokens:
        return []
    singles = [sum(word, []) for word in phonemize([t.lower() for t in tokens])]
    sentence = [phone for chunk in phonemize([text])[0] for phone in chunk]
    flat = [phone for phones in singles for phone in phones]
    owner = [index for index, phones in enumerate(singles) for _ in phones]
    forms: list[list[str]] = [[] for _ in tokens]
    for _, own, heard in edit_ops(flat, sentence, same):
        if own is not None and heard is not None:
            forms[owner[own]].append(sentence[heard])
    return [
        RefWord(token, tuple(form or single))
        for token, form, single in zip(tokens, forms, singles, strict=True)
    ]


def espeak() -> Phonemize:
    import espeakng_loader
    from phonemizer import phonemize
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    from phonemizer.separator import Separator

    EspeakWrapper.set_library(espeakng_loader.get_library_path())
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    separator = Separator(phone=" ", word="|", syllable="")

    def run(texts: list[str]) -> list[list[list[str]]]:
        out = phonemize(
            texts,
            language="en-us",
            backend="espeak",
            separator=separator,
            strip=True,
            preserve_punctuation=False,
            with_stress=False,
            njobs=1,
        )
        return [[w.split() for w in line.split("|") if w.split()] for line in out]

    return run
