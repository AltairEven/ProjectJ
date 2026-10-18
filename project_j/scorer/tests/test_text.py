from project_j_scorer.text import RefWord, espeak, reference_phones, words

SINGLE = {
    "sat": ["s", "æ", "t"],
    "on": ["ɑ", "n"],
    "the": ["ð", "ə"],
    "mat": ["m", "æ", "t"],
    "paper": ["p", "eɪ", "p", "ɚ"],
    "a": ["eɪ"],
}


def fake(sentence_chunks):
    def phonemize(texts):
        if len(texts) == 1 and " " in texts[0]:
            return [sentence_chunks]
        return [[SINGLE[text]] for text in texts]

    return phonemize


def test_words_keep_apostrophes_and_drop_punctuation():
    assert words("Mia's car. It's red!") == ["Mia's", "car", "It's", "red"]


def test_merged_function_words_are_split_back_keeping_sentence_forms():
    chunks = [["s", "æ", "t"], ["ɔ", "n", "ð", "ə"], ["m", "æ", "t"]]
    assert reference_phones("Sat on the mat.", fake(chunks)) == [
        RefWord("Sat", ("s", "æ", "t")),
        RefWord("on", ("ɔ", "n")),
        RefWord("the", ("ð", "ə")),
        RefWord("mat", ("m", "æ", "t")),
    ]


def test_linking_sounds_are_dropped_and_weak_forms_kept():
    chunks = [["p", "eɪ", "p", "ɚ", "ɹ"], ["ɔ", "n", "ð", "ə"], ["m", "æ", "t"]]
    assert [w.phones for w in reference_phones("paper on the mat", fake(chunks))] == [
        ("p", "eɪ", "p", "ɚ"),
        ("ɔ", "n"),
        ("ð", "ə"),
        ("m", "æ", "t"),
    ]


def test_a_word_missing_from_the_sentence_keeps_its_own_phones():
    chunks = [["s", "æ", "t"], ["m", "æ", "t"]]
    assert [w.phones for w in reference_phones("sat a mat", fake(chunks))] == [
        ("s", "æ", "t"),
        ("eɪ",),
        ("m", "æ", "t"),
    ]


def test_espeak_keeps_the_weak_form_in_a_sentence():
    sentence = espeak()(["She wants a bridge."])[0]
    assert len(sentence) == 4 and sentence[2] == ["ɐ"]
