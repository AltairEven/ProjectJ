import asyncio
import hashlib
import logging

import pytest

from project_j.platform.files import PrivateFiles, file_stem, stems
from project_j.platform.models.contracts import solid_png
from project_j.platform.storage import SimulationStore, StorageBlocked
from project_j.platform.tts.recorded import decode_wav, encode_wav, speak
from project_j.platform.tts.simulated import SimulatedSynthesizer
from project_j.platform.visuals import MAX_SVG_BYTES, checked_svg, image_type

SVG = '<svg xmlns="http://www.w3.org/2000/svg"'


def test_wav_round_trip():
    pcm = b"\x01\x00" * 480
    assert decode_wav(encode_wav(pcm, 24000)) == (24000, pcm)
    with pytest.raises(ValueError):
        decode_wav(b"not a wav")


def test_speak_plays_recorded_parts_and_synthesizes_the_rest(caplog):
    recorded = {"Listen.": b"\x02\x00" * 24000}

    async def run():
        return [chunk async for chunk in speak(("先听。", "Listen."), recorded, Synth())]

    class Synth(SimulatedSynthesizer):
        said: list[str] = []

        def stream(self, text, *, seed=None):
            self.said.append(text)
            return super().stream(text, seed=seed)

    with caplog.at_level(logging.INFO):
        chunks = asyncio.run(run())
    audio = b"".join(chunks)
    assert Synth.said == ["先听。"]  # Recorded parts are never synthesized again.
    assert audio.endswith(recorded["Listen."]) and len(audio) > len(recorded["Listen."])
    assert len(chunks[-1]) < len(recorded["Listen."])  # Played in chunks, not one block.
    assert "recorded=1" in caplog.text and "outcome=completed" in caplog.text
    assert "Listen." not in caplog.text and "先听" not in caplog.text


def test_closing_speak_stops_early(caplog):
    async def run():
        stream = speak(("Listen.",), {"Listen.": b"\x00\x00" * 240000}, SimulatedSynthesizer())
        first = await anext(stream)
        await stream.aclose()
        return first

    with caplog.at_level(logging.INFO):
        assert asyncio.run(run())
    assert "outcome=cancelled" in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        f"{SVG}><script>x()</script></svg>",
        f'{SVG} onload="x()"/>',
        f'{SVG} xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="http://e/x.svg#a"/></svg>',
        f'{SVG}><use href="data:image/svg+xml,x"/></svg>',
        f"{SVG}><foreignObject/></svg>",
        f"{SVG}><style>*{{}}</style></svg>",
        f'{SVG}><rect fill="url(http://e/p)"/></svg>',
        f'{SVG}><a href="javascript:x()"/></svg>',
        f'{SVG}><image href="#a"/></svg>',
        f'{SVG}><use href="#a"><set attributeName="xlink:href" to="#b"/></use></svg>',
        '<html xmlns="http://www.w3.org/1999/xhtml"/>',
        "not xml",
        f"{SVG}>{'<g/>' * MAX_SVG_BYTES}</svg>",
    ],
)
def test_unsafe_svg_is_refused(bad):
    with pytest.raises(ValueError):
        checked_svg(bad)


def test_animated_svg_passes():
    svg = (
        f'{SVG} viewBox="0 0 10 10"><defs><linearGradient id="g"><stop offset="0"/>'
        '</linearGradient></defs><circle r="2" fill="url(#g)" style="opacity:0.8">'
        '<animate attributeName="r" values="1;3;1" dur="2s" repeatCount="indefinite"/>'
        '</circle><use href="#g"/></svg>'
    )
    assert checked_svg(svg) == svg.encode()


def test_image_type():
    assert image_type(solid_png()) == "png"
    assert image_type(b"\xff\xd8\xff\xe0" + b"0" * 10) == "jpeg"
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    for bad in (b"GIF89a", b"<svg/>", b""):
        with pytest.raises(ValueError):
            image_type(bad)


def test_put_writes_named_files_and_checks_them(tmp_path):
    store = SimulationStore(tmp_path / "simulation", "run1")
    files = PrivateFiles(store)
    path = "drafts/2026-10-28-abcd1234/audio/voice-goal.v1.wav"
    manifest = files.put(path, b"RIFF-1")
    assert manifest == {"path": path, "sha256": hashlib.sha256(b"RIFF-1").hexdigest(), "bytes": 6}
    assert (store.run / path).stat().st_mode & 0o777 == 0o600
    assert files.read_binary(manifest) == b"RIFF-1"
    story = files.put("lessons/lesson-001-2026-10-28/story.md", "# Zoë’s Bridge\n\nShe ran.\n")
    assert files.read_asset(story) == "# Zoë’s Bridge\n\nShe ran.\n"
    replaced = files.put(path, b"RIFF-2")  # A named file is replaced in place.
    with pytest.raises(ValueError):
        files.read_binary(manifest)  # The old record no longer fits and says so.
    assert files.read_binary(replaced) == b"RIFF-2"
    for bad in ("assets/x.wav", "../x.wav", "lessons", "drafts/../x.wav", "drafts/../../x"):
        with pytest.raises(ValueError):
            files.put(bad, b"x")
    store.active_activity_id = "class"
    with pytest.raises(StorageBlocked):
        files.put(path, b"x")
    store.active_activity_id = None
    store.close()


def test_file_names_follow_resource_ids():
    assert file_stem("voice:place-check") == "voice-place-check"
    assert file_stem("demo:read-aloud-1") == "demo-read-aloud-1"
    assert file_stem("../日本") == "item"
    assert stems(["voice:goal", "voice-goal", "voice:goal2"]) == {
        "voice:goal": "voice-goal",
        "voice-goal": "voice-goal-2",
        "voice:goal2": "voice-goal2",
    }


def test_removing_drafts_keeps_the_current_course_and_lessons(tmp_path):
    store = SimulationStore(tmp_path / "simulation", "run1")
    files = PrivateFiles(store)
    files.put("drafts/a/story.r1.md", "A")
    files.put("drafts/b/audio/x.v1.wav", b"x")
    files.put("lessons/lesson-001-2026-10-28/story.md", "L")
    assert files.remove_drafts(keep="drafts/a") == ["b"]
    assert (store.run / "drafts/a/story.r1.md").exists() and not (store.run / "drafts/b").exists()
    assert (store.run / "lessons/lesson-001-2026-10-28/story.md").exists()
    assert files.remove("drafts/a/story.r1.md") is True
    assert files.remove("drafts/a/story.r1.md") is False
    assert files.remove_tree("drafts/a") == 0 and not (store.run / "drafts/a").exists()
    assert files.remove_tree("drafts/missing") == 0
    store.close()
