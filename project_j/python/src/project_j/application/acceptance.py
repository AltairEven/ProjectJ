"""Build-bound evidence from the final automated checks, never a child qualification."""

import hashlib
import json
from pathlib import Path


def fingerprint(root: Path) -> str:
    files = list((root / "project_j/python/src").rglob("*.py"))
    files += list((root / "project_j/python/tests").rglob("*.py"))
    files += list((root / "project_j/python/tests/fixtures").glob("*.json"))
    files += list((root / "project_j/web/tests").glob("*.mjs"))
    files += list((root / "courses/paper-bridge").glob("*.md"))
    files += list((root / "courses/paper-bridge").glob("*.json"))
    for pattern in ("*.tsx", "*.ts", "*.json", "*.mjs", "*.css"):
        files += list((root / "project_j/web/src").rglob(pattern))
    for directory in (
        "project_j/web",
        "project_j/web/codegen",
        "project_j/web/schema",
        "project_j/scripts",
    ):
        files += [path for path in (root / directory).iterdir() if path.is_file()]
    files += [
        root / "project_j/python/uv.lock",
        root / "project_j/python/pyproject.toml",
        root / "project_j/web/package-lock.json",
    ]
    hasher = hashlib.sha256()
    for path in sorted(set(files)):
        hasher.update(str(path.relative_to(root)).encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def simulation_evidence(root: Path) -> dict:
    path = root / "project_j/.cache/simulation-verification.json"
    if not path.exists():
        return {"result": "unknown", "reason": "The project checks have not run yet."}
    evidence = json.loads(path.read_text())
    if evidence.get("fingerprint") != fingerprint(root) or evidence.get("result") != "pass":
        return {
            "result": "unknown",
            "reason": "The code changed; the earlier checks no longer count.",
        }
    if evidence.get("source_type") != "simulated" or evidence.get("qualification") != "not_granted":
        raise ValueError("invalid simulation-only verification evidence")
    return evidence
