from project_j_scorer import rules


def test_equivalence_is_per_group_and_not_transitive():
    assert rules.same("ɐ", "ə") and rules.same("t", "ɾ") and rules.same("k", "k")
    assert rules.same("ɑː", "ɔ") and rules.same("ɑː", "ɑːɹ")
    assert not rules.same("ɔ", "ɑːɹ") and not rules.same("t", "k")
    assert rules.variants("ɑː") == {"ɑː", "ɔ", "ɔː", "ɑːɹ"} and rules.variants("k") == {"k"}
    assert rules.same("iə", "iː") and rules.same("iə", "ɪ") and rules.same("iː", "i")


def test_systematic_notation_and_accent_variants_found_across_voices_are_equivalent():
    for a, b in [("ɐ", "æ"), ("ɔ", "ʌ"), ("ɔɪ", "oːɹ"), ("ɔɪ", "ɔːɹ"), ("z", "s"), ("ᵻ", "ɪ")]:
        assert rules.same(a, b), (a, b)
    assert rules.same("ɔː", "ɑː") and not rules.same("æ", "ə") and not rules.same("b", "p")
    assert {"can", "could", "will", "would", "must"} <= rules.FUNCTION_WORDS


def test_scoring_config_is_versioned_and_pinned():
    assert rules.THRESHOLDS == "provisional-1"
    assert set(rules.MODEL_FILES) == {
        "config.json",
        "preprocessor_config.json",
        "vocab.json",
        "pytorch_model.bin",
    }
    assert rules.BLANK in rules.SPECIALS and "a" in rules.FUNCTION_WORDS
