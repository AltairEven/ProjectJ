import asyncio
from contextlib import aclosing

import pytest

from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.simulated import SimulatedSynthesizer


def test_simulated_synthesis_streams_silence_by_length_and_rejects_empty_text():
    async def chunks(text):
        async with aclosing(SimulatedSynthesizer().stream(text)) as stream:
            return [chunk async for chunk in stream]

    assert [len(c) for c in asyncio.run(chunks("你好"))] == [24000]
    assert [len(c) for c in asyncio.run(chunks("一二三四五六七八九十"))] == [24000, 24000, 24000]
    with pytest.raises(SynthesisFailed) as error:
        asyncio.run(chunks("  "))
    assert error.value.reason == "empty_text"
