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
SCORER_PROJECT = ROOT / "project_j/scorer"
TTS_PROJECT = ROOT / "project_j/tts"


# What the stamp always says; the main page shows it as is (ADR-0021: English).
STAMP = {
    "source_type": "simulated",
    "evidence": "automated_simulation_checks",
    "scope": "simulation_only",
    "qualification": "not_granted",
    "actual_api": "unverified",
    "actual_voice": "unverified",
}


def main() -> int:
    # Deliberately remove the key from all test subprocesses. Tests use explicit SDK mocks.
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    report_dir = ROOT / "project_j/.cache"
    report_dir.mkdir(parents=True, exist_ok=True)
    ruff = [sys.executable, "-m", "ruff"]
    main_paths = ["--config", "pyproject.toml", "src", "tests", "../scripts"]
    scorer_paths = ["--config", "../scorer/pyproject.toml", "../scorer/src", "../scorer/tests"]
    tts_paths = ["--config", "../tts/pyproject.toml", "../tts/src", "../tts/tests"]
    commands = [
        [*ruff, "check", *main_paths],
        [*ruff, "format", "--check", *main_paths],
        [*ruff, "check", *scorer_paths],
        [*ruff, "format", "--check", *scorer_paths],
        [*ruff, "check", *tts_paths],
        [*ruff, "format", "--check", *tts_paths],
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
    # Each speech sub-project (ADR-0015 scorer, ADR-0016 TTS) has its own locked environment;
    # offline, no real model is loaded.
    for project, report in ((SCORER_PROJECT, "scorer-tests.xml"), (TTS_PROJECT, "tts-tests.xml")):
        completed = subprocess.run(
            [
                str(project / ".venv/bin/python"),
                "-m",
                "pytest",
                f"--junitxml={report_dir / report}",
            ],
            cwd=project,
            env=env | {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            check=False,
        )
        if completed.returncode:
            return completed.returncode
    from project_j.application.acceptance import fingerprint

    count = sum(
        int(suite.attrib.get("tests", 0))
        for report in ("simulation-tests.xml", "scorer-tests.xml", "tts-tests.xml")
        for suite in ElementTree.parse(report_dir / report).getroot().iter("testsuite")
    )
    evidence = STAMP | {
        "result": "pass",
        "fingerprint": fingerprint(ROOT),
        "tests": count,
        "verified_at_utc": datetime.now(UTC).isoformat(),
    }
    (report_dir / "simulation-verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "Simulation checks passed. Actual API/voice, real study use "
        "and teaching effect: unverified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
