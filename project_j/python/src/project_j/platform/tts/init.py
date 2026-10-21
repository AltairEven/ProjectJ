"""project-j-tts-init: download, probe and admit the local speech synthesizer (ADR-0016)."""

import hashlib
import wave
from collections.abc import Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path

from project_j.platform.local_process import init_main
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.local import (
    MODELS,
    LocalSynthesizer,
    SynthesizerUnavailable,
)

SAMPLES = {
    "zh": "小朋友，今天我们一起读一个关于小桥的故事。",
    "en": "Mia has a toy car. She wants a bridge for her car.",
    "mixed": "这个单词是 bridge，意思是桥。我们一起读：bridge。",
}
SAMPLE_LABELS = {"zh": "中文", "en": "英文", "mixed": "中英混读"}
CANCEL_TEXT = SAMPLES["zh"] * 8
CANCEL_LIMIT_MS = 2000
SEED = 7
MIN_AUDIO_SECONDS = 0.5
AUDIBLE_PEAK = 300  # PCM16 amplitude; anything quieter counts as silence.


def peak(pcm: bytes) -> int:
    return max(map(abs, memoryview(pcm).cast("h")), default=0)


async def synthesize(
    synthesizer: LocalSynthesizer, text: str, seed: int | None = None, limit: int | None = None
) -> tuple[dict, bytes]:
    """One synthesis judged by the audio actually received; limit closes the stream early."""
    synthesizer.last_stats = {}
    pcm, status = bytearray(), "ok"
    try:
        async with aclosing(synthesizer.stream(text, seed=seed)) as stream:
            async for chunk in stream:
                pcm += chunk
                if limit and synthesizer.last_stats["chunks"] >= limit:
                    break
    except SynthesisFailed as error:
        status = error.reason
    stats = synthesizer.last_stats
    seconds = round(len(pcm) / 2 / synthesizer.sample_rate, 2)
    elapsed = stats.get("elapsed_ms")
    return {
        "status": status,
        "first_chunk_ms": stats.get("first_chunk_ms"),
        "elapsed_ms": elapsed,
        "audio_seconds": seconds,
        "rtf": round(elapsed / 1000 / seconds, 2) if elapsed and seconds else None,
        "chunks": stats.get("chunks"),
        "peak": peak(bytes(pcm)),
    }, bytes(pcm)


def judge(record: dict) -> list[str]:
    reasons = []
    for name, sample in record["samples"].items():
        label = SAMPLE_LABELS[name]
        if sample["status"] != "ok":
            reasons.append(f"{label}合成失败：{sample['status']}")
        elif sample["audio_seconds"] < MIN_AUDIO_SECONDS or sample["peak"] < AUDIBLE_PEAK:
            reasons.append(f"{label}合成结果无声或过短")
    stop = record["cancel"]["stop_ms"]
    if stop is None:
        reasons.append("中途取消未确认停止")
    elif stop > CANCEL_LIMIT_MS:
        reasons.append(f"中途取消后停止生成用时{stop}毫秒，超过{CANCEL_LIMIT_MS}毫秒")
    if not record["recovered"]:
        reasons.append("取消后合成进程未恢复服务")
    return reasons


async def probe(root: Path, synthesizer: LocalSynthesizer | None = None) -> dict:
    synthesizer = synthesizer or LocalSynthesizer(root, inherit_stderr=True)
    manifest = root / MODELS / "manifest.json"
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "identity": None,
        "device": None,
        "sample_rate": None,
        "load_ms": None,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest.is_file()
        else None,
        "samples": {},
        "cancel": {"stop_ms": None},
        "recovered": False,
        "seed_repeatable": None,
        "memory_peak_bytes": None,
        "passed": False,
        "reasons": [],
    }
    try:
        ready = await synthesizer.start()
    except SynthesizerUnavailable as error:
        record["reasons"] = [f"合成进程无法启动：{error}"]
        LocalSynthesizer.save_init(root, record)
        return record
    record |= {
        "identity": LocalSynthesizer.identity(ready),
        "device": ready.get("device"),
        "sample_rate": ready.get("sample_rate"),
        "load_ms": ready.get("load_ms"),
    }
    memory = [ready.get("memory_bytes")]
    try:
        for name, text in SAMPLES.items():
            record["samples"][name], _ = await synthesize(synthesizer, text)
            memory.append(synthesizer.last_stats.get("memory_bytes"))
        await synthesize(synthesizer, CANCEL_TEXT, limit=1)
        record["cancel"] = {"stop_ms": synthesizer.last_stats.get("cancel_ms")}
        # Recorded only: whether a fixed seed repeats is input for presynthesis (ADR-0016 §7).
        first, first_pcm = await synthesize(synthesizer, SAMPLES["en"], seed=SEED)
        second, second_pcm = await synthesize(synthesizer, SAMPLES["en"], seed=SEED)
        record["recovered"] = first["status"] == "ok"
        if first["status"] == second["status"] == "ok":
            record["seed_repeatable"] = first_pcm == second_pcm
    finally:
        await synthesizer.aclose()
    record["memory_peak_bytes"] = max((m for m in memory if m is not None), default=None)
    record["reasons"] = judge(record)
    record["passed"] = not record["reasons"]
    LocalSynthesizer.save_init(root, record)
    return record


def show(record: dict):
    for reason in record["reasons"]:
        print(f"- {reason}")
    if record.get("device"):
        peak_bytes = record.get("memory_peak_bytes")
        memory = f"{peak_bytes / 1e9:.2f} GB" if peak_bytes else "未记录"
        print(f"设备 {record['device']}；加载 {record['load_ms']} 毫秒；内存峰值 {memory}")
    for name, sample in record["samples"].items():
        slow = "（慢于实时，边生成边播放会断续）" if (sample["rtf"] or 0) > 1 else ""
        print(
            f"- {SAMPLE_LABELS[name]}：{sample['status']}，时长 {sample['audio_seconds']} 秒，"
            f"首段 {sample['first_chunk_ms']} 毫秒，RTF {sample['rtf']}{slow}"
        )
    stop = record["cancel"]["stop_ms"]
    print(f"中途取消后停止生成：{stop} 毫秒" if stop is not None else "中途取消：未确认停止")
    repeat = {True: "相同", False: "不同", None: "未能比较"}[record["seed_repeatable"]]
    print(f"固定种子两次合成：{repeat}（只记录，供预合成参考）")
    print(f"记录时间 {record['captured_at']}")
    print('音色是否合适由成人试听：project-j-tts-init speak --text "…" --out 文件.wav')


async def speak(root: Path, command: Sequence[str] | None, text: str, out: Path) -> int:
    """Adult listening check: write one sentence to a new WAV file, never overwriting."""
    if out.exists():
        print(f"{out} 已存在，不覆盖。")
        return 1
    synthesizer = LocalSynthesizer(root, command, inherit_stderr=True)
    try:
        await synthesizer.start()
    except SynthesizerUnavailable as error:
        print(error)
        return 1
    pcm = bytearray()
    try:
        async with aclosing(synthesizer.stream(text)) as stream:
            async for chunk in stream:
                pcm += chunk
    except SynthesisFailed as error:
        print(f"合成失败：{error.reason}")
        return 1
    finally:
        await synthesizer.aclose()
    try:
        with out.open("xb") as target, wave.open(target, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(synthesizer.sample_rate)
            audio.writeframes(bytes(pcm))
    except FileExistsError:
        print(f"{out} 已存在，不覆盖。")
        return 1
    seconds = len(pcm) / 2 / synthesizer.sample_rate
    first = synthesizer.last_stats.get("first_chunk_ms")
    print(f"已写入 {out}：时长 {seconds:.2f} 秒，首段 {first} 毫秒。")
    return 0


def main(argv: list[str] | None = None, command: Sequence[str] | None = None) -> int:
    def arguments(parser):
        parser.add_argument("--text", required=True)
        parser.add_argument("--out", type=Path, required=True)

    return init_main(
        LocalSynthesizer,
        "本机语音合成初始化：下载模型、探针与准入（ADR-0016）。",
        "tts",
        probe,
        show,
        (
            "speak",
            "成人试听：把一句话合成为新的WAV文件，不覆盖",
            arguments,
            lambda root, command, args: speak(root, command, args.text, args.out),
        ),
        argv,
        command,
    )
