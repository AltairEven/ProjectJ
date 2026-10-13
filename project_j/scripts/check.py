"""Unified simulation checks; run with the locked project environment."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
PYTHON_PROJECT = ROOT / "project_j/python"


def main() -> int:
    # Deliberately remove the key from all test subprocesses. Tests use explicit SDK mocks.
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    report_dir = ROOT / "project_j/.cache"
    report_dir.mkdir(parents=True, exist_ok=True)
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
        [
            str(PYTHON_PROJECT / ".venv/bin/project-j-export-schema"),
            str(ROOT / "project_j/web/schema/openapi.json"),
        ],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "generate:api"],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "check:types"],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "test:interface"],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "build"],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "test:docs"],
        ["npm", "run", "--prefix", str(ROOT / "project_j/web"), "check:docs"],
        [
            sys.executable,
            "-m",
            "pytest",
            f"--junitxml={report_dir / 'simulation-tests.xml'}",
        ],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=PYTHON_PROJECT, env=env, check=False)
        if completed.returncode:
            return completed.returncode
    from project_j.application.acceptance import fingerprint

    suites = ElementTree.parse(report_dir / "simulation-tests.xml").getroot()
    count = sum(int(suite.attrib.get("tests", 0)) for suite in suites.iter("testsuite"))
    evidence = {
        "source_type": "simulated",
        "evidence": "automated_simulation_checks",
        "result": "pass",
        "fingerprint": fingerprint(ROOT),
        "tests": count,
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "scope": "simulation_only",
        "qualification": "not_granted",
        "actual_api": "未验证",
        "actual_voice": "未验证",
    }
    (report_dir / "simulation-verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "Simulation checks passed. Actual API/voice, adult durations, "
        "child qualification and teaching effect: unverified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
