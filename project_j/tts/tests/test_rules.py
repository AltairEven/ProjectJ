from project_j_tts import rules


def test_generation_is_capped_by_text_length():
    assert rules.max_tokens_for("") == 63
    assert rules.max_tokens_for("好" * 20) == 188
    assert rules.max_tokens_for("a" * 1000) == rules.MAX_TOKENS == 4096


def test_voice_and_model_are_versioned_and_pinned():
    assert (rules.VOICE, rules.SPEAKER, rules.LANGUAGE) == ("teacher-1", "serena", "auto")
    assert rules.INSTRUCT == "语气温柔亲切，像给小学生上课的老师，吐字清楚，语速稍慢。"
    assert len(rules.MODEL_FILES) == 12
    assert "speech_tokenizer/model.safetensors" in rules.MODEL_FILES
    assert rules.SAMPLE_RATE == 24000
