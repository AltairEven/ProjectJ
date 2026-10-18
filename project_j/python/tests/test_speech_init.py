import asyncio
import sys
from pathlib import Path

from project_j.platform.speech.init import SAMPLES, main, probe
from project_j.platform.speech.local import INIT, MODELS, LocalScorer, file_reasons

FAKE = Path(__file__).parent / "fixtures/fake_scorer.py"


def fake(root, mode="ok"):
    return LocalScorer(root, [sys.executable, str(FAKE), mode])


def text_synth(text):
    return text.encode()


def with_manifest(root):
    manifest = root / MODELS / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}")


def test_passing_probe_writes_a_private_record_and_admits(tmp_path):
    with_manifest(tmp_path)
    record = asyncio.run(probe(tmp_path, fake(tmp_path), text_synth))
    assert record["passed"] and record["reasons"] == []
    assert record["samples"]["misread"]["flagged"] == ["bridge"]
    assert record["samples"]["correct"]["flagged"] == [] and "long" in record["samples"]
    assert record["memory_peak_bytes"] == 200 and record["identity"]["revision"] == "r1"
    assert (tmp_path / INIT).stat().st_mode & 0o777 == 0o600
    assert file_reasons(tmp_path) == []


def test_a_missed_misread_fails_the_probe(tmp_path):
    with_manifest(tmp_path)

    def no_misread(text):
        return text.replace("brige", "bridge").encode()

    record = asyncio.run(probe(tmp_path, fake(tmp_path), no_misread))
    assert not record["passed"] and "bridge未标出" in record["reasons"][0]
    assert file_reasons(tmp_path)[0].startswith("评分探针未通过")


def test_missing_voice_or_unloadable_scorer_fails_with_a_reason(tmp_path):
    def no_say(text):
        raise FileNotFoundError("say")

    record = asyncio.run(probe(tmp_path, fake(tmp_path), no_say))
    assert "需要macOS语音Samantha" in record["reasons"][0]
    record = asyncio.run(probe(tmp_path, fake(tmp_path, "not_ready"), text_synth))
    assert record["reasons"][0].startswith("评分进程无法启动")


def test_changed_models_need_a_new_probe(tmp_path):
    with_manifest(tmp_path)
    asyncio.run(probe(tmp_path, fake(tmp_path), text_synth))
    (tmp_path / MODELS / "manifest.json").write_text('{"revision": "other"}')
    assert "评分模型已改动" in file_reasons(tmp_path)[0]


def test_status_and_adult_score_commands(tmp_path, monkeypatch, capsys):
    (tmp_path / "project_j").mkdir()
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 1 and "评分模型未下载" in capsys.readouterr().out
    wav = tmp_path / "me.wav"
    wav.write_bytes(b"brige")
    command = [sys.executable, str(FAKE), "ok"]
    assert main(["score", str(wav), "--text", SAMPLES["correct"]], command) == 0
    out = capsys.readouterr().out
    assert "bridge" in out and "mispronounced" in out and "门槛未校准" in out
    assert not (tmp_path / INIT).exists()
