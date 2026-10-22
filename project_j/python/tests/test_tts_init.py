import asyncio
import hashlib
import json
import stat
import sys
import wave
from pathlib import Path

from project_j.platform.tts.init import main, probe
from project_j.platform.tts.local import INIT, MODELS, LocalSynthesizer

FAKE = Path(__file__).parent / "fixtures/fake_tts.py"


def fake(mode="ok"):
    return [sys.executable, str(FAKE), mode]


def synth(root, mode="ok", **options):
    return LocalSynthesizer(root, fake(mode), **options)


def project(tmp_path, monkeypatch):
    (tmp_path / "project_j").mkdir()
    monkeypatch.chdir(tmp_path)


def test_probe_records_timings_cancel_and_seed(tmp_path):
    manifest = tmp_path / MODELS / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    record = asyncio.run(probe(tmp_path, synth(tmp_path)))
    assert record["passed"], record["reasons"]
    assert set(record["samples"]) == {"story", "task"}
    story = record["samples"]["story"]
    assert story["status"] == "ok" and story["audio_seconds"] >= 0.5 and story["peak"] >= 300
    assert story["first_chunk_ms"] >= 0 and story["rtf"] > 0 and story["chunks"] >= 1
    assert record["cancel"]["stop_ms"] <= 2000 and record["recovered"] is True
    assert record["seed_repeatable"] is True and record["identity"]["voice"] == "teacher-1"
    assert record["manifest_sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert record["memory_peak_bytes"] == 200 and record["sample_rate"] == 24000
    assert stat.S_IMODE((tmp_path / INIT).stat().st_mode) == 0o600
    assert json.loads((tmp_path / INIT).read_text()) == record


def test_probe_fails_on_silent_audio(tmp_path):
    record = asyncio.run(probe(tmp_path, synth(tmp_path, "silent")))
    assert not record["passed"] and "the story sample is silent or too short" in record["reasons"]


def test_probe_fails_when_cancel_is_not_acknowledged(tmp_path):
    stuck = synth(tmp_path, "stuck_cancel", chunk_timeout=0.5, cancel_timeout=0.3)
    record = asyncio.run(probe(tmp_path, stuck))
    assert not record["passed"]
    assert {"the cancel was not confirmed", "the process did not recover after the cancel"} <= set(
        record["reasons"]
    )


def test_probe_without_a_process_records_why(tmp_path):
    record = asyncio.run(probe(tmp_path, synth(tmp_path, "not_ready")))
    assert not record["passed"] and record["reasons"][0].startswith(
        "the speech process cannot start"
    )
    assert (tmp_path / INIT).is_file()


def test_speak_writes_a_wav_and_never_overwrites(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch)
    assert main(["speak", "--text", "你好", "--out", "a.wav"], command=fake()) == 0
    with wave.open(str(tmp_path / "a.wav")) as audio:
        shape = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
        assert shape == (24000, 1, 2) and audio.getnframes() == 12000
    written = (tmp_path / "a.wav").read_bytes()
    assert main(["speak", "--text", "再见", "--out", "a.wav"], command=fake()) == 1
    assert (tmp_path / "a.wav").read_bytes() == written and "不覆盖" in capsys.readouterr().out


def test_status_lists_what_is_missing(tmp_path, monkeypatch, capsys):
    project(tmp_path, monkeypatch)
    assert main(["status"]) == 1
    out = capsys.readouterr().out
    assert "合成器未就绪" in out and "project-j-tts-init download" in out


def test_samples_are_english_only():
    from project_j.english import chinese
    from project_j.platform.tts.init import SAMPLES

    assert SAMPLES == {
        "story": "Mia has a toy car. She wants a bridge for her car.",
        "task": "Which bus did Ben try first? Tap your answer.",
    }
    assert not chinese(SAMPLES)
