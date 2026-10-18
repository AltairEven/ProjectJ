"""Versioned scoring configuration (ADR-0015): pinned model, provisional thresholds, groups.

Changing any threshold, group or word list changes how a reading is judged: bump THRESHOLDS.
"""

SCORER = "local-gop"
VERSION = "1"
THRESHOLDS = "provisional-1"

MODEL_REPO = "facebook/wav2vec2-lv-60-espeak-cv-ft"
MODEL_REVISION = "ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4"
MODEL_FILES = {
    "config.json": "4609fb49b7e1d28aecb2840da1926c40bd915bc6f1120a940afacf7159bbfb13",
    "preprocessor_config.json": "a2254a5b58f72cd4de3632f8eee64f3f098b7c1402128d2f419e7d00ae13e335",
    "vocab.json": "d732ab2456c0c017930001dc9af0b41b3b93d25b2eb9740bf9d925508d7d87d0",
    "pytorch_model.bin": "3173bde9e9ce490fa0f989e413c42f25bc1820c020adc1e6b9b87025b3cfcc5e",
}

SAMPLE_RATE = 16000
FRAME_SECONDS = 0.02
MAX_AUDIO_SECONDS = 310  # The 300 s product limit plus margin for an auto-stopped recording.
CHUNK_SECONDS = 40.0
CONTEXT_SECONDS = 2.0

# Provisional, tried only on synthesized adult speech; lenient by design.
MISREAD_BELOW = 5.0
OMIT_DELETED_AT_LEAST = 0.75
ABSENT_BELOW = 50.0  # A phone free decoding drops counts as deleted only if it also scores below.
INSERT_MIN_PHONES = 4
PAUSE_SECONDS = 0.5
MIN_DECODED_PHONES = 3

BLANK = "<pad>"
SPECIALS = frozenset({"<pad>", "<s>", "</s>", "<unk>"})

# Accent and notation differences judged as the same sound; not transitive. Beyond the spike's
# groups, the rest are substitutions found on correct readings by most of six macOS voices.
GROUPS = (
    frozenset({"ɐ", "ə", "ʌ"}),
    frozenset({"ɑː", "ɔ", "ɔː"}),
    frozenset({"ɜː", "ɚ"}),
    frozenset({"iː", "ɪ", "i", "iə"}),  # espeak's iə is usually heard as iː (+ ə or ɚ).
    frozenset({"ɾ", "t", "d"}),
    frozenset({"ɑːɹ", "ɑː"}),
    frozenset({"ɐ", "æ"}),  # Weak and strong "has".
    frozenset({"ɔ", "ʌ"}),  # "wants".
    frozenset({"ɔɪ", "oːɹ", "ɔːɹ"}),  # The model's spelling of "toy".
    frozenset({"z", "s"}),  # Final devoicing: "says", "stays".
    frozenset({"ᵻ", "ɪ", "ə"}),  # Reduced vowel: "between", "buses".
)

FUNCTION_WORDS = frozenset(
    "a an the to of and or for in on at is are was were be am it he she we you they i me him "
    "her us them his its my your our their with as by from that this but so do does did has "
    "have had not can could will would shall should may might must".split()
)


def same(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in GROUPS)


def variants(phone: str) -> frozenset[str]:
    return frozenset({phone}).union(*(group for group in GROUPS if phone in group))
