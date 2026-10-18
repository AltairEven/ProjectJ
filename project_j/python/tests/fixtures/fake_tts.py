"""Fake synthesis process speaking the project-j-tts protocol; tests only."""

import base64
import hashlib
import json
import os
import queue
import sys
import threading
import time

MODE = sys.argv[1]
IDENTITY = {
    "synthesizer": "local-qwen3-tts",
    "version": "1",
    "voice": "teacher-1",
    "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
    "revision": "r1",
    "device": "gpu",
}


def say(message):
    print(json.dumps(message), flush=True)


def pattern(text):
    """0.5 s of PCM16 that tells which text it came from and is never silent."""
    if MODE == "silent":
        return bytes(24000)
    if MODE == "env":
        return (b"KEY!" if "OPENAI_API_KEY" in os.environ else b"OKAY") * 6000
    return hashlib.sha256(text.encode()).digest()[:4] * 6000


def is_cancel(line, request_id):
    message = json.loads(line)
    return message.get("op") == "cancel" and message.get("id") == request_id


if MODE == "not_ready":
    say({"ready": False, "error": "digest_mismatch", "detail": "model.safetensors"})
    sys.exit(1)
say({"ready": True, **IDENTITY, "sample_rate": 24000, "load_ms": 5, "memory_bytes": 100})
lines = queue.Queue()


def read():
    for line in sys.stdin:
        lines.put(line)
    lines.put(None)


threading.Thread(target=read, daemon=True).start()
pending = []
while True:
    line = pending.pop(0) if pending else lines.get()
    if line is None:
        break
    request = json.loads(line)
    request_id = request["id"]
    if request.get("op") == "cancel":
        say({"id": request_id, "cancelled": True, "chunks": None})
        continue
    text = request["text"]
    if not text.strip():
        say({"id": request_id, "error": "empty_text"})
        continue
    sent, stopped = 0, False
    for _ in range(max(1, len(text) // 10)):
        time.sleep(0.02)
        audio = base64.b64encode(pattern(text)).decode()
        say({"id": request_id, "chunk": sent, "audio": audio})
        sent += 1
        if MODE == "exit_mid_stream":
            sys.exit(0)
        if MODE == "stuck_cancel":
            time.sleep(3600)
        while True:
            try:
                queued = lines.get_nowait()
            except queue.Empty:
                break
            if not stopped and queued is not None and is_cancel(queued, request_id):
                stopped = True
            else:
                pending.append(queued)
        if stopped:
            break
    if stopped:
        say({"id": request_id, "cancelled": True, "chunks": sent})
        continue
    say(
        {
            "id": request_id,
            "done": True,
            "chunks": sent,
            "audio_seconds": round(0.5 * sent, 2),
            "truncated": MODE == "truncated",
            "memory_bytes": 200,
        }
    )
