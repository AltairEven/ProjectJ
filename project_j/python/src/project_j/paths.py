"""Resolve paths inside an explicit data-space root; nothing may escape it."""

from pathlib import Path


def check_path(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("private file path escapes explicit root")
    return candidate
