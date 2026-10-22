import asyncio
import hashlib
import json
import logging
import sys
import time
from contextlib import aclosing
from pathlib import Path

import pytest

from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.local import INIT, MODELS, LocalSynthesizer, SynthesizerUnavailable

FAKE = Path(__file__).parent / "fixtures/fake_tts.py"
MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"


def synth(tmp_path, mode="ok", **options):
    return LocalSynthesizer(tmp_path, [sys.executable, str(FAKE), mode], **options)


def pattern(text):
    return hashlib.sha256(text.encode()).digest()[:4] * 6000


async def collect(local, text, limit=None):
    chunks = []
    async with aclosing(local.stream(text)) as stream:
        async for chunk in stream:
            chunks.append(chunk)
            if limit and len(chunks) >= limit:
                break
    return chunks


async def until_failure(local, text):
    chunks = []
    with pytest.raises(SynthesisFailed) as error:
        async with aclosing(local.stream(text)) as stream:
            async for chunk in stream:
                chunks.append(chunk)
    return chunks, error.value.reason


def write_init(root, revision="r1", passed=True, reasons=(), manifest_sha256=None):
    manifest = root / MODELS / "manifest.json"
    record = {
        "passed": passed,
        "reasons": list(reasons),
        "manifest_sha256": manifest_sha256 or hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "identity": {
            "synthesizer": "local-qwen3-tts",
            "version": "1",
            "voice": "teacher-1",
            "model": MODEL,
            "revision": revision,
        },
    }
    (root / INIT).write_text(json.dumps(record))


def test_stream_yields_chunks_with_identity_and_stats(tmp_path):
    async def go():
        local = synth(tmp_path)
        ready = await local.start()
        chunks = await collect(local, "Mia has a toy car.")
        await local.aclose()
        return local, ready, chunks

    local, ready, chunks = asyncio.run(go())
    assert chunks == [pattern("Mia has a toy car.")] and ready["device"] == "gpu"
    assert (local.service, local.version, local.voice) == ("local-qwen3-tts", "1", "teacher-1")
    assert local.model == f"{MODEL}@r1" and local.sample_rate == 24000
    stats = local.last_stats
    assert stats["first_chunk_ms"] >= 0 and stats["elapsed_ms"] >= stats["first_chunk_ms"]
    assert (stats["chunks"], stats["audio_seconds"], stats["truncated"]) == (1, 0.5, False)
    assert stats["memory_bytes"] == 200 and stats["cancel_ms"] is None


def test_closing_a_stream_cancels_it_and_the_next_stream_is_clean(tmp_path):
    async def go():
        local = synth(tmp_path)
        await local.start()
        first = await collect(local, "好" * 400, limit=1)
        cancel_ms = local.last_stats["cancel_ms"]
        second = await collect(local, "hi")
        await local.aclose()
        return first, cancel_ms, second

    first, cancel_ms, second = asyncio.run(go())
    assert first == [pattern("好" * 400)] and cancel_ms is not None
    assert second == [pattern("hi")]


def test_a_process_that_dies_mid_stream_fails_fast(tmp_path):
    async def go():
        local = synth(tmp_path, "exit_mid_stream")
        await local.start()
        received = await until_failure(local, "x" * 30)
        again = await until_failure(local, "hi")
        return local, received, again

    local, (chunks, reason), again = asyncio.run(go())
    assert chunks == [pattern("x" * 30)] and reason == "failed"
    assert again == ([], "not_ready") and "Speech process has stopped" in local.reasons()


def test_truncated_and_refused_text_raise_after_what_was_delivered(tmp_path):
    async def go():
        truncating, refusing = synth(tmp_path, "truncated"), synth(tmp_path)
        await truncating.start()
        await refusing.start()
        results = (
            await until_failure(truncating, "Mia has a toy car."),
            await until_failure(refusing, "  "),
        )
        await truncating.aclose()
        await refusing.aclose()
        return results

    truncated, refused = asyncio.run(go())
    assert truncated == ([pattern("Mia has a toy car.")], "truncated")
    assert refused == ([], "empty_text")


def test_a_cancel_that_is_never_acknowledged_kills_and_restarts_the_process(tmp_path):
    async def go():
        local = synth(tmp_path, "stuck_cancel", cancel_timeout=0.3)
        await local.start()
        started = time.perf_counter()
        await collect(local, "x" * 30, limit=1)
        elapsed, killed = time.perf_counter() - started, local.exited
        reasons = local.reasons()
        await local.reviving
        alive = not local.exited and local.process.returncode is None
        await local.aclose()
        return elapsed, killed, reasons, alive

    elapsed, killed, reasons, alive = asyncio.run(go())
    assert elapsed < 2 and killed and alive
    assert "Speech process is restarting; please wait" in reasons


def test_a_dead_process_comes_back_in_the_background(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")
    command = [sys.executable, str(FAKE), "exit_once", str(tmp_path / "died")]

    async def go():
        local = LocalSynthesizer(tmp_path, command)
        await local.start()
        died = await until_failure(local, "x" * 30)
        meanwhile = await until_failure(local, "hi")  # Fails fast while the new one loads.
        await local.reviving
        again = await collect(local, "hi")
        await local.aclose()
        return died, meanwhile, again

    died, meanwhile, again = asyncio.run(go())
    assert died[1] == "failed" and meanwhile == ([], "not_ready")
    assert again == [pattern("hi")]
    assert "synthesizer revive outcome=ready ms=" in caplog.text


def test_the_child_process_gets_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def go():
        local = synth(tmp_path, "env")
        await local.start()
        chunks = await collect(local, "hi")
        await local.aclose()
        return chunks

    assert asyncio.run(go()) == [b"OKAY" * 6000]


def test_a_not_ready_process_raises_unavailable(tmp_path):
    with pytest.raises(
        SynthesizerUnavailable,
        match=r"Speech process not ready: digest_mismatch \(model.safetensors\)",
    ):
        asyncio.run(synth(tmp_path, "not_ready").start())


def test_reasons_follow_models_probe_record_and_identity(tmp_path):
    local = synth(tmp_path)
    assert local.reasons() == [
        "Speech model not downloaded; run project-j-tts-init download",
        "Speech process not started",
    ]
    manifest = tmp_path / MODELS / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    assert local.reasons()[0] == "Speech not probed yet; run project-j-tts-init probe"
    write_init(tmp_path, passed=False, reasons=["the story sample failed: failed"])
    assert local.reasons()[0] == "Speech probe failed: the story sample failed: failed"
    write_init(tmp_path, manifest_sha256="0" * 64)
    assert local.reasons()[0] == "Speech model changed; run project-j-tts-init probe again"

    async def running(revision):
        write_init(tmp_path, revision=revision)
        await local.start()
        try:
            return local.reasons()
        finally:
            await local.aclose()

    assert asyncio.run(running("r2")) == [
        "Speech: the version, voice setup or model no longer matches the probe record; "
        "run project-j-tts-init probe again"
    ]
    assert asyncio.run(running("r1")) == []


def test_synthesis_logs_each_stream_outcome_without_the_text(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="project_j")

    async def go():
        local = synth(tmp_path)
        await local.start()
        await collect(local, "Mia has a toy car.")
        await collect(local, "Mia has a toy car.", limit=1)
        await local.aclose()

    asyncio.run(go())
    assert "synthesizer ready device=gpu" in caplog.text
    assert "synthesis outcome=completed chars=18 first_chunk_ms=" in caplog.text
    assert "synthesis outcome=cancelled" in caplog.text and "Mia" not in caplog.text


def test_closing_never_restarts_a_process_that_ends_mid_stream(tmp_path):
    async def go():
        local = synth(tmp_path, "stuck_cancel")
        await local.start()
        job = asyncio.create_task(collect(local, "x" * 30))
        await asyncio.sleep(0.3)  # The child is now silent mid-stream.
        await local.aclose()
        with pytest.raises(SynthesisFailed):
            await job
        await asyncio.sleep(0.1)
        return local

    local = asyncio.run(go())
    assert local.reviving is None and local.exited
