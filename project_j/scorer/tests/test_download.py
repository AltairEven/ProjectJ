import hashlib
import json

import pytest

from project_j_scorer.download import download
from project_j_scorer.model import ModelError, verify

CONTENT = {"config.json": b"{}", "pytorch_model.bin": b"weights"}
FILES = {name: hashlib.sha256(data).hexdigest() for name, data in CONTENT.items()}


def fake_fetch(served):
    def fetch(repo_id, filename, revision, cache_dir):
        path = cache_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(served[filename])
        return str(path)

    return fetch


def test_download_verifies_and_writes_a_private_manifest(tmp_path):
    models = tmp_path / "scorer/models"
    manifest = download(models, files=FILES, fetch=fake_fetch(CONTENT))
    assert manifest["files"]["pytorch_model.bin"] == {
        "sha256": FILES["pytorch_model.bin"],
        "bytes": 7,
    }
    assert verify(models) == manifest
    assert (models.stat().st_mode & 0o777) == 0o700
    assert all((models / n).stat().st_mode & 0o777 == 0o600 for n in (*CONTENT, "manifest.json"))
    assert not (models / ".hf").exists() and not (tmp_path / "scorer/models.staging").exists()


def test_a_mismatched_file_is_refused_and_existing_models_stay(tmp_path):
    models = tmp_path / "models"
    download(models, files=FILES, fetch=fake_fetch(CONTENT))
    before = (models / "manifest.json").read_bytes()
    with pytest.raises(ModelError) as caught:
        download(models, files=FILES, fetch=fake_fetch(CONTENT | {"config.json": b"evil"}))
    assert caught.value.kind == "digest_mismatch"
    assert (models / "manifest.json").read_bytes() == before
    assert not (tmp_path / "models.staging").exists()


def test_verify_reports_missing_and_tampered_models(tmp_path):
    with pytest.raises(ModelError) as missing:
        verify(tmp_path / "none")
    assert missing.value.kind == "models_missing"
    models = tmp_path / "models"
    download(models, files=FILES, fetch=fake_fetch(CONTENT))
    (models / "pytorch_model.bin").write_bytes(b"changed")
    with pytest.raises(ModelError) as tampered:
        verify(models)
    assert tampered.value.kind == "digest_mismatch"
    assert json.loads((models / "manifest.json").read_text())["revision"]
