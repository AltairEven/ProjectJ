"""Versioned synthesis configuration (ADR-0016): pinned model, Jane's voice, generation limits.

Changing any voice value changes how Jane sounds: bump VOICE. Changing a runtime limit: bump
VERSION.
"""

import math

SYNTHESIZER = "local-qwen3-tts"
VERSION = "1"

# Jane's voice: preset speaker plus the teacher instruction; Jane speaks English only
# (ADR-0021). Sampling values are passed explicitly so a library default change cannot alter it.
VOICE = "teacher-2"
SPEAKER = "serena"
INSTRUCT = "Warm and kind, like a teacher reading to a young learner; clear and a little slow."
LANGUAGE = "english"
TEMPERATURE = 0.9
TOP_K = 50
TOP_P = 1.0
REPETITION_PENALTY = 1.05

MODEL_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"
MODEL_REVISION = "52f4770fd9726457eae3d3b6aa92047a25a10776"
MODEL_FILES = {
    "config.json": "9c0f62abb48c432361fdcd54da1fd9fe00a993c0acccef5d45c2d3edd5932fb1",
    "generation_config.json": "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa",
    "merges.txt": "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3",
    "model.safetensors": "3a791fb8250fc32ab0259b679d834159d3c8516af62f033ff2b9f42913e3fab6",
    "model.safetensors.index.json": (
        "abf019e063dcc595d7aef7c9554d2806ffdc58746a7ac6238cb26177af8d431f"
    ),
    "preprocessor_config.json": "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119",
    "speech_tokenizer/config.json": (
        "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"
    ),
    "speech_tokenizer/configuration.json": (
        "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd"
    ),
    "speech_tokenizer/model.safetensors": (
        "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"
    ),
    "speech_tokenizer/preprocessor_config.json": (
        "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb"
    ),
    "tokenizer_config.json": "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670",
    "vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
}

SAMPLE_RATE = 24000
TOKENS_PER_SECOND = 12.5
STREAM_SECONDS = 0.32  # About 4 speech tokens per chunk.
MAX_TEXT_CHARS = 1000

MIN_AUDIO_SECONDS = 5.0
SECONDS_PER_CHAR = 0.5
MAX_TOKENS = 4096


def max_tokens_for(text: str) -> int:
    """Cap generation so a model stuck repeating ends; the teacher-2 probe read English at about
    0.09 s a character (2026-10-22), well inside the 0.5 s allowance.

    ponytail: a length heuristic with headroom; tune from probe and adult runs if real lines
    are truncated.
    """
    seconds = MIN_AUDIO_SECONDS + SECONDS_PER_CHAR * len(text)
    return min(MAX_TOKENS, math.ceil(TOKENS_PER_SECOND * seconds))
