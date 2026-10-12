"""P0 offline checks; run with the locked project environment."""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PYTHON_PROJECT = ROOT / "project_j/python"


def check_document_links() -> int:
    errors = []
    docs = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for folder in ("design", "docs", "decisions"):
        docs.extend((ROOT / folder).rglob("*.md"))
    for doc in docs:
        for href in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text()):
            href = href.strip("<>")
            parsed = urlsplit(href)
            if parsed.scheme:
                continue  # Network pages are checked separately; this never uses the network.
            target = (doc.parent / unquote(parsed.path)).resolve() if parsed.path else doc
            if not target.exists():
                errors.append(f"{doc.relative_to(ROOT)}: missing {href}")
                continue
            if parsed.fragment and target.suffix == ".md":
                headings = re.findall(r"^#{1,6}\s+(.+)$", target.read_text(), re.MULTILINE)
                anchors = {re.sub(r"[^\w\- ]", "", x.lower()).replace(" ", "-") for x in headings}
                if unquote(parsed.fragment) not in anchors:
                    errors.append(f"{doc.relative_to(ROOT)}: missing anchor {href}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Local document links: {len(docs)} files passed (no network/qualification checks)")
    return 0


def main() -> int:
    if sys.version_info[:3] != (3, 14, 4):
        print(
            "Use project Python 3.14.4; runtime upgrades require updating the pin/check evidence."
        )
        return 1
    if check_document_links():
        return 1
    # Deliberately remove the key from all test subprocesses. Tests use explicit SDK mocks.
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    commands = [
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            "pyproject.toml",
            "src",
            "tests",
            "../scripts",
        ],
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            "pyproject.toml",
            "src",
            "tests",
            "../scripts",
        ],
        [sys.executable, "-m", "pytest"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=PYTHON_PROJECT, env=env, check=False)
        if completed.returncode:
            return completed.returncode
    print(
        "P0 offline checks passed. Full E2E01, actual API/voice and child eligibility: unverified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
