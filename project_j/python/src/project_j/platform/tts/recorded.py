"""Audio prepared before class (ADR-0016): WAV files at rest, PCM in memory while teaching.

A sentence Jane says is played from its adopted recording when there is one, otherwise
synthesized live; the order of the parts is the order the core approved.
"""

import io
import logging
import wave
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import aclosing

from project_j.platform.tts.contracts import SynthesisFailed, Synthesizer

log = logging.getLogger(__name__)
CHUNK_SECONDS = 0.32  # The live synthesizer's own chunk length.


def encode_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def decode_wav(data: bytes) -> tuple[int, bytes]:
    """Sample rate and mono 16-bit PCM of a prepared recording."""
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError("prepared audio must be mono 16-bit PCM")
            return source.getframerate(), source.readframes(source.getnframes())
    except (wave.Error, EOFError) as error:
        raise ValueError("prepared audio is not a readable WAV") from error


async def speak(
    parts: Sequence[str], recorded: Mapping[str, bytes], synthesizer: Synthesizer
) -> AsyncIterator[bytes]:
    """Close it (contextlib.aclosing) to cancel; live synthesis stops with it."""
    outcome = "cancelled"
    step = 2 * round(CHUNK_SECONDS * synthesizer.sample_rate)
    try:
        for part in parts:
            if part in recorded:
                audio = recorded[part]
                for start in range(0, len(audio), step):
                    yield audio[start : start + step]
            else:
                async with aclosing(synthesizer.stream(part)) as live:
                    async for chunk in live:
                        yield chunk
        outcome = "completed"
    except SynthesisFailed as error:
        outcome = f"failed:{error.reason}"
        raise
    finally:
        log.info(
            "speech parts=%d recorded=%d outcome=%s",
            len(parts),
            sum(part in recorded for part in parts),
            outcome,
        )
