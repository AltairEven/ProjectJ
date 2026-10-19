"""Checks on generated pictures and animations before anything is saved or shown.

An animation is SVG the model wrote: only drawing and SMIL animation elements, no scripts,
event attributes, style elements, foreign content or links outside the drawing itself.
"""

import re
from xml.etree import ElementTree

MAX_SVG_BYTES = 200_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line",
        "polyline", "polygon", "text", "tspan", "linearGradient", "radialGradient", "stop",
        "clipPath", "mask", "use", "animate", "animateTransform", "animateMotion", "set", "mpath",
    }
)  # fmt: skip
MEDIA_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "wav": "audio/wav",
}
URL = re.compile(r"url\(\s*['\"]?([^)'\"]*)")


def local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def checked_svg(text: str) -> bytes:
    """The SVG bytes when every element and attribute is allowed; ValueError otherwise."""
    data = text.encode()
    if len(data) > MAX_SVG_BYTES:
        raise ValueError("动画超过大小上限")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("动画不是有效的SVG") from error
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise ValueError("动画的根元素须为SVG")
    for element in root.iter():
        if not element.tag.startswith(f"{{{SVG_NAMESPACE}}}") or (
            local(element.tag) not in SVG_ELEMENTS
        ):
            raise ValueError(f"动画含不允许的元素：{local(str(element.tag))}")
        for name, value in element.attrib.items():
            attribute, compact = local(name), re.sub(r"\s", "", value).lower()
            if attribute.lower().startswith("on"):
                raise ValueError("动画含事件属性")
            if attribute == "href" and not value.startswith("#"):
                raise ValueError("动画含外部链接")
            if attribute == "attributeName" and local(value.replace(":", "}")) == "href":
                raise ValueError("动画不能改写链接")
            if "javascript:" in compact or any(
                not target.startswith("#") for target in URL.findall(value)
            ):
                raise ValueError("动画引用了外部内容")
    return data


def image_type(data: bytes) -> str:
    """png, jpeg or webp by the file's own header; ValueError for anything else."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片超过大小上限")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("生成结果不是图片")
