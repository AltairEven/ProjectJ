"""P0 offline checks; run with the locked project environment."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_PROJECT = ROOT / "project_j/python"


def main() -> int:
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
