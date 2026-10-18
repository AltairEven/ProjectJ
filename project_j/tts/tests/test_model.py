import hashlib
import json
import sys

import pytest

from project_j_tts.cli import main
from project_j_tts.model import ModelError, QwenEngine


def test_engine_rejects_an_unpinned_manifest_before_loading_mlx(tmp_path):
    (tmp_path / "config.json").write_bytes(b"{}")
    files = {"config.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2}}
    manifest = {"repo": "someone/else", "revision": "v", "files": files}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ModelError) as error:
        QwenEngine(tmp_path)
    assert error.value.kind == "digest_mismatch" and "mlx" not in sys.modules


def test_engine_reports_missing_models(tmp_path):
    with pytest.raises(ModelError) as error:
        QwenEngine(tmp_path)
    assert error.value.kind == "models_missing"


def test_download_command_reports_a_digest_failure(tmp_path, monkeypatch, capsys):
    def refuse(models, cache_dir=None):
        raise ModelError("digest_mismatch", "vocab.json")

    monkeypatch.setattr("project_j_tts.download.download", refuse)
    assert main(["download", "--models", str(tmp_path / "m")]) == 1
    assert "模型核对未通过：digest_mismatch（vocab.json）" in capsys.readouterr().err
