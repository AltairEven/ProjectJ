"""Simulated synthesis: silence as long as the text would take; no process, no model."""

from project_j.platform.tts.contracts import SynthesisFailed

CHUNK_SECONDS = 0.5
SECONDS_PER_CHAR = 0.15


class SimulatedSynthesizer:
    service = "simulated-synthesis"
    version = "1"
    simulated = True
    model = None
    voice = "simulated"
    sample_rate = 24000

    def reasons(self) -> list[str]:
        return []

    async def stream(self, text: str, *, seed: int | None = None):
        if not text.strip():
            raise SynthesisFailed("empty_text")
        remaining = round(max(0.5, SECONDS_PER_CHAR * len(text.strip())) * self.sample_rate)
        step = round(CHUNK_SECONDS * self.sample_rate)
        while remaining > 0:
            samples = min(step, remaining)
            remaining -= samples
            yield bytes(2 * samples)
