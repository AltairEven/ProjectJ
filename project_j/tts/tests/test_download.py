import hashlib
import json
import stat

import pytest

from project_j_tts.download import download
from project_j_tts.model import ModelError, verify

CONTENT = {
    "config.json": b"{}",
    "vocab.json": b'{"a": 1}',
    "speech_tokenizer/model.safetensors": b"weights",
}
FILES = {name: hashlib.sha256(data).hexdigest() for name, data in CONTENT.items()}


def fetcher(tmp_path, content=CONTENT, fail=None):
    def fetch(repo_id, filename, revision, cache_dir):
        if filename == fail:
            raise OSError("network down")
        path = tmp_path / "cache" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content[filename])
        return str(path)

    return fetch


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_download_verifies_nested_files_and_writes_a_private_manifest(tmp_path):
    models = tmp_path / "private/models"
    manifest = download(models, fetch=fetcher(tmp_path), files=FILES, repo="r", revision="v")
    assert manifest["revision"] == "v" and set(manifest["files"]) == set(FILES)
    assert json.loads((models / "manifest.json").read_text()) == manifest
    assert mode(models / "speech_tokenizer") == 0o700
    assert mode(models / "speech_tokenizer/model.safetensors") == 0o600
    assert mode(models / "manifest.json") == 0o600
    assert not (tmp_path / "private/models.staging").exists()
    assert verify(models) == manifest


def test_a_mismatched_or_failed_file_leaves_existing_models_untouched(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "marker.txt").write_text("old")
    wrong = CONTENT | {"vocab.json": b"tampered"}
    with pytest.raises(ModelError) as error:
        download(models, fetch=fetcher(tmp_path, wrong), files=FILES, repo="r", revision="v")
    assert error.value.kind == "digest_mismatch" and error.value.detail == "vocab.json"
    with pytest.raises(OSError):
        download(models, fetch=fetcher(tmp_path, fail="config.json"), files=FILES)
    assert (models / "marker.txt").read_text() == "old"
    assert not (tmp_path / "models.staging").exists()


def test_verify_reports_missing_and_tampered_files(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    with pytest.raises(ModelError) as error:
        verify(models)
    assert error.value.kind == "models_missing"
    download(models, fetch=fetcher(tmp_path), files=FILES, repo="r", revision="v")
    (models / "vocab.json").write_bytes(b'{"a": 2}')
    with pytest.raises(ModelError) as error:
        verify(models)
    assert error.value.kind == "digest_mismatch"
    (models / "vocab.json").write_bytes(CONTENT["vocab.json"])
    (models / "speech_tokenizer/model.safetensors").unlink()
    with pytest.raises(ModelError) as error:
        verify(models)
    assert (error.value.kind, error.value.detail) == (
        "models_missing",
        "speech_tokenizer/model.safetensors",
    )
