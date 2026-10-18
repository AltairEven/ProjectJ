import asyncio
import hashlib
import json
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
    assert again == ([], "not_ready") and "合成进程已退出" in local.reasons()


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


def test_a_cancel_that_is_never_acknowledged_kills_the_process(tmp_path):
    async def go():
        local = synth(tmp_path, "stuck_cancel", cancel_timeout=0.3)
        await local.start()
        started = time.perf_counter()
        await collect(local, "x" * 30, limit=1)
        return local, time.perf_counter() - started

    local, elapsed = asyncio.run(go())
    assert elapsed < 2 and local.exited and "合成进程已退出" in local.reasons()


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
        SynthesizerUnavailable, match="合成进程未就绪：digest_mismatch（model.safetensors）"
    ):
        asyncio.run(synth(tmp_path, "not_ready").start())


def test_reasons_follow_models_probe_record_and_identity(tmp_path):
    local = synth(tmp_path)
    assert local.reasons() == [
        "合成模型未下载，请运行 project-j-tts-init download",
        "合成进程未启动",
    ]
    manifest = tmp_path / MODELS / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    assert local.reasons()[0] == "合成器尚未探测，请运行 project-j-tts-init probe"
    write_init(tmp_path, passed=False, reasons=["中文合成失败：failed"])
    assert local.reasons()[0] == "合成探针未通过：中文合成失败：failed"
    write_init(tmp_path, manifest_sha256="0" * 64)
    assert local.reasons()[0] == "合成模型已改动，请重新运行 project-j-tts-init probe"

    async def running(revision):
        write_init(tmp_path, revision=revision)
        await local.start()
        try:
            return local.reasons()
        finally:
            await local.aclose()

    assert asyncio.run(running("r2")) == [
        "合成器版本、声音配置或模型与探针记录不一致，请重新运行 project-j-tts-init probe"
    ]
    assert asyncio.run(running("r1")) == []
