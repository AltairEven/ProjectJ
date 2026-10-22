"""The student sees, hears and answers in English only (ADR-0021).

CHINESE checks what the program or a model shows the student: Han, CJK punctuation and
full-width forms. HAN checks what the student typed: only Han counts, so full-width punctuation
from a Chinese input method is not taken for a Chinese answer.
"""

import re

CHINESE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
HAN = re.compile(r"[㐀-䶿一-鿿]")
ENGLISH_ONLY = "Please answer in English."


def chinese(value: object, skip: frozenset[str] = frozenset()) -> bool:
    """Any text inside value (dict values, list and tuple items) has Chinese; the values of
    dict keys in skip, such as the student's own words quoted verbatim, are not looked into."""
    if isinstance(value, str):
        return bool(CHINESE.search(value))
    if isinstance(value, dict):
        return any(chinese(item, skip) for key, item in value.items() if key not in skip)
    if isinstance(value, list | tuple):
        return any(chinese(item, skip) for item in value)
    return False


def han(text: str) -> bool:
    return bool(HAN.search(text))
