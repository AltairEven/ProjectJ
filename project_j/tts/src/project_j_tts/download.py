"""Download the pinned model files, verify each digest, then swap them in atomically."""

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from huggingface_hub import hf_hub_download

from project_j_tts.model import ModelError, sha256_file
from project_j_tts.rules import MODEL_FILES, MODEL_REPO, MODEL_REVISION


def private_dir(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def download(
    models: Path,
    cache_dir: Path | None = None,
    *,
    repo: str = MODEL_REPO,
    revision: str = MODEL_REVISION,
    files: Mapping[str, str] = MODEL_FILES,
    fetch=hf_hub_download,
) -> dict:
    """The only network step of the synthesizer; existing models stay untouched on any failure."""
    staging = models.parent / f"{models.name}.staging"
    old = models.parent / f"{models.name}.old"
    shutil.rmtree(staging, ignore_errors=True)
    private_dir(models.parent)
    staging.mkdir(mode=0o700)
    staging.chmod(0o700)
    try:
        entries = {}
        for name, expected in files.items():
            source = fetch(
                repo_id=repo,
                filename=name,
                revision=revision,
                cache_dir=cache_dir or staging / ".hf",
            )
            target = staging / name
            for parent in reversed(target.relative_to(staging).parents[:-1]):
                private_dir(staging / parent)  # e.g. speech_tokenizer/
            shutil.copyfile(source, target)
            target.chmod(0o600)
            digest = sha256_file(target)
            if digest != expected:
                raise ModelError("digest_mismatch", name)
            entries[name] = {"sha256": digest, "bytes": target.stat().st_size}
        manifest = {"repo": repo, "revision": revision, "files": entries}
        fd = os.open(staging / "manifest.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(manifest, stream, indent=2)
        shutil.rmtree(staging / ".hf", ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        if models.exists():
            models.rename(old)
        staging.rename(models)
        shutil.rmtree(old, ignore_errors=True)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
