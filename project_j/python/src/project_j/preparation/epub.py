"""A book as EPUB 3 with the standard library only: every model-written text is escaped, pictures
are typed by their own header, and the language is English so readers offer the dictionary."""

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from project_j.platform.visuals import image_type

EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}
CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)


@dataclass(frozen=True)
class Page:
    title: str
    paragraphs: list[str]
    pictures: dict[int, bytes]  # The paragraph (from 1) each picture follows.


def xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">'
        f'<head><meta charset="utf-8"/><title>{escape(title)}</title></head>'
        f"<body>{body}</body></html>"
    )


def build_epub(identifier: str, title: str, pages: list[Page], modified: datetime) -> bytes:
    files: dict[str, bytes] = {}
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine, links = [], []
    for number, page in enumerate(pages, start=1):
        body = [f"<h1>{escape(page.title)}</h1>"]
        for paragraph, text in enumerate(page.paragraphs, start=1):
            body.append(f"<p>{escape(text)}</p>")
            if picture := page.pictures.get(paragraph):
                kind = image_type(picture)
                href = f"images/c{number}-p{paragraph}.{EXTENSIONS[kind]}"
                files[href] = picture
                items.append(
                    f'<item id="i{number}-{paragraph}" href="{href}" media-type="image/{kind}"/>'
                )
                body.append(f'<p><img src="{href}" alt=""/></p>')
        href = f"chapter-{number}.xhtml"
        files[href] = xhtml(page.title, "".join(body)).encode()
        items.append(f'<item id="c{number}" href="{href}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{number}"/>')
        links.append(f'<li><a href="{href}">{escape(page.title)}</a></li>')
    nav = f'<nav epub:type="toc"><h1>{escape(title)}</h1><ol>{"".join(links)}</ol></nav>'
    files["nav.xhtml"] = xhtml(title, nav).encode()
    stamp = modified.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    files["content.opf"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="id">urn:project-j:{escape(identifier)}</dc:identifier>'
        f"<dc:title>{escape(title)}</dc:title><dc:language>en</dc:language>"
        f'<meta property="dcterms:modified">{stamp}</meta></metadata>'
        f"<manifest>{''.join(items)}</manifest><spine>{''.join(spine)}</spine></package>"
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        book.writestr("META-INF/container.xml", CONTAINER, zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            book.writestr(f"OEBPS/{name}", data, zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
