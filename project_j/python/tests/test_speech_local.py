import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

import pytest

from project_j.platform.speech.local import INIT, MODELS, LocalScorer, ScorerUnavailable

FAKE = Path(__file__).parent / "fixtures/fake_scorer.py"


def scorer(tmp_path, mode="ok"):
    return LocalScorer(tmp_path, [sys.executable, str(FAKE), mode])


def write_init(root, identity_revision="r1", passed=True):
    manifest = root / MODELS / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}")
    record = {
        "passed": passed,
        "reasons": [],
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "identity": {
            "scorer": "local-gop",
            "version": "1",
            "thresholds": "provisional-1",
            "model": "facebook/wav2vec2-lv-60-espeak-cv-ft",
            "revision": identity_revision,
        },
    }
    (root / INIT).write_text(json.dumps(record))


def test_scores_map_to_results_with_identity_and_limitations(tmp_path):
    async def go():
        local = scorer(tmp_path)
        ready = await local.start()
        result = await local.score(b"... brige ...", "She wants a bridge.")
        empty = await local.score(b"", "She wants a bridge.")
        await local.aclose()
        return local, ready, result, empty

    local, ready, result, empty = asyncio.run(go())
    assert ready["device"] == "cpu" and local.version == "1" and local.thresholds == "provisional-1"
    assert local.model == "facebook/wav2vec2-lv-60-espeak-cv-ft@r1"
    bridge = next(w for w in result.words if w.word == "bridge")
    assert bridge.error == "mispronounced" and bridge.min_phone == 1.0
    assert result.limitations == ("本机评分器，门槛未校准",) and not result.simulated
    assert empty.status == "unknown" and empty.reason == "no_speech"
    assert local.last_stats == {"elapsed_ms": 7, "memory_bytes": 200}


def test_a_scorer_that_cannot_load_is_unavailable(tmp_path):
    local = scorer(tmp_path, "not_ready")
    with pytest.raises(ScorerUnavailable, match="digest_mismatch"):
        asyncio.run(local.start())
    assert "评分进程未启动" in local.reasons()


def test_a_dead_process_gives_unknown_without_hanging(tmp_path):
    async def go():
        local = scorer(tmp_path, "exit_after_ready")
        await local.start()
        first = await asyncio.wait_for(local.score(b"x", "car"), 5)
        second = await asyncio.wait_for(local.score(b"x", "car"), 5)
        return local, first, second

    local, first, second = asyncio.run(go())
    assert first.status == second.status == "unknown" and first.reason == "failed"
    assert "评分进程已退出" in local.reasons()


def test_a_cancelled_request_does_not_leak_its_late_reply(tmp_path):
    async def go():
        local = scorer(tmp_path, "slow")
        await local.start()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(local.score(b"x", "first"), 0.1)
        result = await local.score(b"x", "second")
        await local.aclose()
        return result

    assert [w.word for w in asyncio.run(go()).words] == ["second"]


def test_child_environment_carries_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def go():
        local = scorer(tmp_path, "env")
        await local.start()
        result = await local.score(b"x", "car")
        await local.aclose()
        return result

    assert asyncio.run(go()).words[0].word == "clean"


def test_reasons_follow_files_and_the_probed_identity(tmp_path):
    async def go(local):
        await local.start()
        reasons = local.reasons()
        await local.aclose()
        return reasons

    assert "评分模型未下载" in scorer(tmp_path).reasons()[0]
    write_init(tmp_path)
    assert asyncio.run(go(scorer(tmp_path))) == []
    write_init(tmp_path, identity_revision="r0")
    assert "不一致" in asyncio.run(go(scorer(tmp_path)))[0]


def test_scoring_logs_round_trip_and_compute_time_without_the_text(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def go():
        local = scorer(tmp_path)
        await local.start()
        await local.score(b"... brige ...", "She wants a bridge.")
        await local.aclose()

    asyncio.run(go())
    assert "scorer ready device=cpu load_ms=5" in caplog.text
    assert "scorer status=scored reason=None audio_bytes=13 round_trip_ms=" in caplog.text
    assert "compute_ms=7" in caplog.text and "bridge" not in caplog.text


def test_a_dead_scorer_comes_back_in_the_background(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    command = [sys.executable, str(FAKE), "exit_once", str(tmp_path / "died")]

    async def go():
        local = LocalScorer(tmp_path, command)
        await local.start()
        died = await local.score(b"x", "car")
        await local.reviving
        again = await local.score(b"x", "car")
        await local.aclose()
        return died, again

    died, again = asyncio.run(go())
    assert (died.status, died.reason) == ("unknown", "failed") and again.status == "scored"
    assert "scorer revive outcome=ready ms=" in caplog.text


def test_warming_scores_silence_and_logs_only_timing(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def go():
        local = scorer(tmp_path)
        await local.start()
        await local.warm()
        result = await local.score(b"x", "car")
        await local.aclose()
        return result

    assert asyncio.run(go()).status == "scored"
    assert "scorer warm status=scored ms=" in caplog.text
