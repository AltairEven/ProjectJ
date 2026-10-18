import time

import numpy as np

IDENTITY = {
    "synthesizer": "local-qwen3-tts",
    "version": "1",
    "voice": "teacher-1",
    "model": "m",
    "revision": "r",
    "device": "gpu",
}


class FakeEngine:
    """Two 0.1 s chunks per text; "long" streams 200 slow chunks, "boom" fails, "loop" truncates."""

    identity = IDENTITY
    load_ms = 5
    sample_rate = 24000

    def __init__(self):
        self.truncated = False
        self.seeds: list[int | None] = []
        self.closed_texts: list[str] = []

    def stream(self, text, seed):
        self.seeds.append(seed)
        self.truncated = False
        if text == "boom":
            raise RuntimeError("boom")
        try:
            for _ in range(200 if text == "long" else 2):
                if text == "long":
                    time.sleep(0.01)
                yield np.full(2400, 0.5, dtype=np.float32)
        except GeneratorExit:
            self.closed_texts.append(text)
            raise
        self.truncated = text == "loop"

    def memory_bytes(self):
        return 123
