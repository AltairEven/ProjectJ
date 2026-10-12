"""Resolve explicit P0 test roots; never create or select the family private area."""

from pathlib import Path
from typing import Literal


def check_path(root: Path, relative: str, realm: Literal["simulation", "adult_check"]) -> Path:
    if realm not in {"simulation", "adult_check"}:
        raise ValueError("P0 cannot access family data")
    root = root.resolve(strict=True)
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("private file path escapes explicit root")
    return candidate
