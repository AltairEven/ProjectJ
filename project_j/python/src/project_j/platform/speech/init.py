"""project-j-scorer-init: download, probe and admit the local pronunciation scorer (ADR-0015)."""

import hashlib
import io
import subprocess
import tempfile
import wave
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from project_j.platform.local_process import init_main
from project_j.platform.speech.local import (
    MODELS,
    LocalScorer,
    ScorerUnavailable,
)

REFERENCE = "Mia has a toy car. She wants a bridge for her car."
SAMPLES = {
    "correct": REFERENCE,
    "misread": "Mia has a toy car. She wants a brige for her car.",
    "long": " [[slnc 2000]] ".join([REFERENCE] * 9),  # 99 words with 2 s pauses; measured only.
}
REFERENCES = {"correct": REFERENCE, "misread": REFERENCE, "long": " ".join([REFERENCE] * 9)}
SAMPLE_LABELS = {"correct": "正确朗读", "misread": "故意读错", "long": "长段慢读"}
ERROR_LABELS = {
    "none": "正常",
    "mispronounced": "疑似读错",
    "omitted": "漏读",
    "inserted": "多读",
}
PROBE_SOURCE = "macOS say（Samantha）在探测时本机生成，用后即删，不保存、不分发"


def synthesize(text: str) -> bytes:
    """Adult synthetic probe audio; init never runs during class and this is no child's voice."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "probe.wav"
        subprocess.run(
            ["say", "-v", "Samantha", "--file-format=WAVE", "--data-format=LEI16@16000"]
            + ["-o", str(path), text],
            check=True,
            capture_output=True,
        )
        return path.read_bytes()


def seconds(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio)) as source:
            return round(source.getnframes() / source.getframerate(), 2)
    except wave.Error, EOFError:
        return None


def judge(samples: dict) -> list[str]:
    reasons = [
        f"{name} sample got no score"
        for name in ("correct", "misread")
        if samples.get(name, {}).get("status") != "scored"
    ]
    if reasons:
        return reasons
    if flagged := samples["correct"]["flagged"]:
        reasons.append("the correct reading was flagged: " + ", ".join(flagged))
    if (flagged := samples["misread"]["flagged"]) != ["bridge"]:
        reasons.append(
            "the misread bridge was not flagged alone: " + (", ".join(flagged) or "none flagged")
        )
    return reasons


async def probe(
    root: Path,
    scorer: LocalScorer | None = None,
    synth: Callable[[str], bytes] = synthesize,
) -> dict:
    scorer = scorer or LocalScorer(root, inherit_stderr=True)
    manifest = root / MODELS / "manifest.json"
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "identity": None,
        "device": None,
        "load_ms": None,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest.is_file()
        else None,
        "samples": {},
        "memory_peak_bytes": None,
        "probe_source": PROBE_SOURCE,
        "passed": False,
        "reasons": [],
    }
    try:
        ready = await scorer.start()
    except ScorerUnavailable as error:
        record["reasons"] = [f"the scorer process cannot start: {error}"]
        LocalScorer.save_init(root, record)
        return record
    record |= {"identity": LocalScorer.identity(ready), "device": ready.get("device")}
    record["load_ms"] = ready.get("load_ms")
    memory = [ready.get("memory_bytes")]
    try:
        for name, text in SAMPLES.items():
            try:
                audio = synth(text)
            except OSError, subprocess.CalledProcessError:
                record["reasons"].append(
                    "cannot make the probe samples (needs the macOS voice Samantha)"
                )
                break
            scorer.last_stats = {}
            result = await scorer.score(audio, REFERENCES[name])
            memory.append(scorer.last_stats.get("memory_bytes"))
            record["samples"][name] = {
                "audio_seconds": seconds(audio),
                "elapsed_ms": scorer.last_stats.get("elapsed_ms"),
                "status": result.status,
                "flagged": [word.word for word in result.words if word.error != "none"],
                "accuracy": result.accuracy,
                "completeness": result.completeness,
            }
    finally:
        await scorer.aclose()
    record["reasons"] = record["reasons"] or judge(record["samples"])
    record["memory_peak_bytes"] = max((m for m in memory if m is not None), default=None)
    record["passed"] = not record["reasons"]
    LocalScorer.save_init(root, record)
    return record


def show(record: dict):
    for reason in record["reasons"]:
        print(f"- {reason}")
    if record.get("device"):
        peak = record.get("memory_peak_bytes")
        memory = f"{peak / 1e9:.2f} GB" if peak else "未记录"
        print(f"设备 {record['device']}；加载 {record['load_ms']} 毫秒；内存峰值 {memory}")
    for name, sample in record["samples"].items():
        flagged = "、".join(sample["flagged"]) or "无"
        print(
            f"- {SAMPLE_LABELS[name]}：录音 {sample['audio_seconds']} 秒，"
            f"评分 {sample['elapsed_ms']} 毫秒，标出：{flagged}"
        )
    print(f"探针样本：{record['probe_source']}；记录时间 {record['captured_at']}")


async def adult_score(root: Path, command: Sequence[str] | None, audio: bytes, text: str) -> int:
    """Adult self-check: print one recording's scores; nothing is saved."""
    scorer = LocalScorer(root, command, inherit_stderr=True)
    try:
        await scorer.start()
    except ScorerUnavailable as error:
        print(error)
        return 1
    try:
        result = await scorer.score(audio, text)
    finally:
        await scorer.aclose()
    if result.status != "scored":
        print(f"未取得评分：{result.reason}")
        return 1
    for word in result.words:
        print(f"{word.word}\t{word.accuracy:.1f}\t{ERROR_LABELS[word.error]}（{word.error}）")
    print(
        f"准确度 {result.accuracy}；完整度 {result.completeness}；"
        f"停顿 {result.pauses} 次；语速 {result.words_per_minute} 词/分钟"
    )
    print("；".join(result.limitations))
    return 0


def main(argv: list[str] | None = None, command: Sequence[str] | None = None) -> int:
    def arguments(parser):
        parser.add_argument("file", type=Path)
        parser.add_argument("--text", required=True)

    return init_main(
        LocalScorer,
        "本机发音评分初始化：下载模型、探针与准入（ADR-0015）。",
        "scorer",
        probe,
        show,
        (
            "score",
            "成人自测：评一段16 kHz单声道16位WAV，不保存",
            arguments,
            lambda root, command, args: adult_score(
                root, command, args.file.read_bytes(), args.text
            ),
        ),
        argv,
        command,
    )
