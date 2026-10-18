"""Fake scorer process speaking the project-j-scorer protocol; tests only."""

import base64
import json
import os
import sys
import time

MODE = sys.argv[1]
IDENTITY = {
    "scorer": "local-gop",
    "version": "1",
    "thresholds": "provisional-1",
    "model": "facebook/wav2vec2-lv-60-espeak-cv-ft",
    "revision": "r1",
    "device": "cpu",
}


def say(message):
    print(json.dumps(message), flush=True)


if MODE == "not_ready":
    say({"ready": False, "error": "digest_mismatch", "detail": "pytorch_model.bin"})
    sys.exit(1)
say({"ready": True, **IDENTITY, "load_ms": 5, "memory_bytes": 100})
if MODE == "exit_after_ready":
    sys.exit(0)
for line in sys.stdin:
    request = json.loads(line)
    if MODE == "slow":
        time.sleep(0.5)
    audio = base64.b64decode(request["audio"])
    misread = b"brige" in audio
    words = [w.strip(".,") for w in request["reference"].split()]
    reply = {
        "status": "scored",
        "accuracy": 90.0,
        "completeness": 100.0,
        "pauses": 0,
        "words_per_minute": 120.0,
        "words": [
            {
                "word": w,
                "accuracy": 2.0 if misread and w == "bridge" else 95.0,
                "min_phone": 1.0 if misread and w == "bridge" else 90.0,
                "error": "mispronounced" if misread and w == "bridge" else "none",
            }
            for w in words
        ],
    }
    if not audio:
        reply = {"status": "unknown", "reason": "no_speech"}
    if MODE == "env":
        reply["words"][0]["word"] = "key" if "OPENAI_API_KEY" in os.environ else "clean"
    say({"id": request["id"], **reply, "elapsed_ms": 7, "memory_bytes": 200})
