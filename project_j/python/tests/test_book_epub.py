"""The book's EPUB 3: layout, metadata, escaping and picture types by header."""

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime

import pytest

from project_j.platform.models.contracts import solid_png
from project_j.preparation.epub import Page, build_epub

NOW = datetime(2026, 11, 20, 10, 15, 0, 123456, tzinfo=UTC)
OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 10


def opened(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def test_epub_layout_and_metadata():
    pages = [
        Page("One & Two", ["A < B & C.", 'Then "quiet".'], {1: solid_png(), 2: WEBP}),
        Page("Three", ["The end."], {}),
    ]
    data = build_epub("b1", 'Link & the "Rain" <1>', pages, NOW)
    with opened(data) as book:
        first = book.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert book.read("mimetype") == b"application/epub+zip"
        container = ET.fromstring(book.read("META-INF/container.xml"))
        assert "OEBPS/content.opf" in ET.tostring(container).decode()
        opf = ET.fromstring(book.read("OEBPS/content.opf"))
        metadata = opf.find(f"{OPF}metadata")
        assert metadata.find(f"{DC}language").text == "en"
        assert metadata.find(f"{DC}title").text == 'Link & the "Rain" <1>'
        assert metadata.find(f"{DC}identifier").text == "urn:project-j:b1"
        modified = [
            m for m in metadata.iter(f"{OPF}meta") if m.get("property") == "dcterms:modified"
        ]
        assert modified[0].text == "2026-11-20T10:15:00Z"
        items = list(opf.find(f"{OPF}manifest"))
        assert [i.get("properties") for i in items if i.get("href") == "nav.xhtml"] == ["nav"]
        names = set(book.namelist())
        assert all(f"OEBPS/{item.get('href')}" in names for item in items)
        types = {item.get("href"): item.get("media-type") for item in items}
        assert sorted(v for k, v in types.items() if k.startswith("images/")) == [
            "image/png",
            "image/webp",
        ]
        spine = [ref.get("idref") for ref in opf.find(f"{OPF}spine")]
        assert len(spine) == 2
        for name in names:
            if name.endswith(".xhtml"):
                page = ET.fromstring(book.read(name))  # every page parses as XML
                assert page.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"
        chapter = book.read("OEBPS/chapter-1.xhtml").decode()
        assert "A &lt; B &amp; C." in chapter and "One &amp; Two" in chapter
        assert chapter.index("A &lt; B") < chapter.index("images/") < chapter.index("Then")


def test_a_picture_that_is_not_png_jpeg_or_webp_is_refused():
    with pytest.raises(ValueError):
        build_epub("b1", "T", [Page("One", ["Text."], {1: b"GIF89a"})], NOW)


def test_control_characters_a_model_slips_in_do_not_break_the_xhtml():
    data = build_epub("b1", "Ti\x00tle", [Page("One\x0b", ["A\x01 B\tC\x1f."], {})], NOW)
    with opened(data) as book:
        chapter = book.read("OEBPS/chapter-1.xhtml")
        ET.fromstring(chapter)  # parses as XML
        assert "<h1>One</h1><p>A B\tC.</p>" in chapter.decode()
        assert ET.fromstring(book.read("OEBPS/content.opf")).find(f".//{DC}title").text == "Title"
