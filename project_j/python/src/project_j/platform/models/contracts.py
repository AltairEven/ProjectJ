"""Provider-neutral model requests and results; adapters absorb provider differences."""

import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue

from project_j.contracts import Contract

# Workflow order; planning and preparation have bindings and admission before they have callers.
Purpose = Literal["planning", "preparation", "classroom_judgment", "evaluation", "vocabulary"]
PURPOSES: tuple[Purpose, ...] = (
    "planning",
    "preparation",
    "classroom_judgment",
    "evaluation",
    "vocabulary",
)
Effort = Literal["low", "medium", "high", "xhigh"]
# Service tier; "fast" trades higher usage for speed where an adapter offers it.
Tier = Literal["default", "fast"]
Check = Literal["verified", "unverified", "unsupported", "mismatch"]
ProbeStatus = Literal["verified", "unverified", "unsupported", "failed", "not_probed"]
Failure = Literal[
    "auth",
    "quota",
    "timeout",
    "refused",
    "incomplete",
    "invalid_output",
    "config_mismatch",
    "unavailable",
    "not_initialized",
]
LABELS = {
    "verified": "verified",
    "unverified": "unverified",
    "unsupported": "not supported",
    "failed": "failed",
    "not_probed": "not probed",
}
PURPOSE_LABELS = {
    "planning": "Planning",
    "preparation": "Lesson making",
    "classroom_judgment": "Class judgment",
    "evaluation": "Evaluation",
    "vocabulary": "Word meanings",
}
ITEM_LABELS = {
    "auth": "Sign-in",
    "model": "Model",
    "structured_output": "Structured output",
    "cancellation": "Cancel",
    "image_input": "Image input",
    "audio": "Audio",
    "tools": "Tools off",
    "tier:fast": "Fast tier",
}
# Requests carry learning stage/state and task data only (ADR-0013).
IDENTITY_KEYS = frozenset(
    {"age", "name", "birth", "birth_month", "birthday", "gender", "learner_name"}
)
REQUIRED: dict[Purpose, tuple[str, ...]] = {
    "planning": ("auth", "model", "structured_output"),
    "preparation": ("auth", "model", "structured_output"),
    "classroom_judgment": ("auth", "model", "structured_output", "cancellation"),
    "evaluation": ("auth", "model", "structured_output"),
    "vocabulary": ("auth", "model", "structured_output"),
}


class ImageNote(Contract):
    """What a generated picture shows, for the parent to check against the request."""

    description: str = Field(min_length=1)


class Binding(Contract):
    adapter: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: Effort
    tier: Tier = "default"


class ConfigCheck(Contract):
    requested: str | None
    actual: str | None
    status: Check


class ProbeItem(Contract):
    status: ProbeStatus
    detail: str = ""


class CapabilityReport(Contract):
    adapter: str
    adapter_version: str
    model: str
    simulated: bool
    captured_at: str
    items: dict[str, ProbeItem]
    calls: list[dict[str, JsonValue]] = []


def item_label(name: str) -> str:
    """Parent-facing name of a probe item."""
    if name.startswith("effort:"):
        return f"Effort {name.removeprefix('effort:')}"
    return ITEM_LABELS.get(name, name)


def identity_keys(value) -> set[str]:
    if isinstance(value, Mapping):
        found = {key for key in value if str(key).casefold() in IDENTITY_KEYS}
        return found.union(*(identity_keys(item) for item in value.values()))
    if isinstance(value, list | tuple):
        return set().union(*(identity_keys(item) for item in value))
    return set()


@dataclass(frozen=True)
class ModelRequest:
    purpose: Purpose
    instructions: str
    context: dict
    output: type[BaseModel]
    deadline_seconds: float = 10.0
    # "image": the reply carries one generated picture besides the structured output.
    media: Literal["image"] | None = None

    def __post_init__(self):
        if self.purpose not in PURPOSES:
            raise ValueError(f"unknown model purpose: {self.purpose}")
        if not self.instructions.strip():
            raise ValueError("a model request needs instructions")
        if found := identity_keys(self.context):
            raise ValueError(f"model requests must not carry identity fields: {sorted(found)}")


@dataclass(frozen=True)
class AdapterReply:
    data: dict | None
    failure: Failure | None = None
    actual: dict[str, ConfigCheck] = field(default_factory=dict)
    usage: dict | None = None
    request_id: str | None = None
    media: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class ModelResult:
    call_id: str
    purpose: Purpose
    binding: Binding | None
    output: BaseModel | None
    failure: Failure | None
    actual: dict[str, ConfigCheck]
    media: tuple[bytes, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None


class ModelAdapter(Protocol):
    adapter_id: str
    version: str
    efforts: tuple[Effort, ...]
    tiers: tuple[Tier, ...]
    simulated: bool

    async def invoke(self, request: ModelRequest, binding: Binding) -> AdapterReply: ...

    async def probe(
        self, model: str, efforts: tuple[Effort, ...], tiers: tuple[Tier, ...] = ()
    ) -> CapabilityReport: ...


def admission(
    report: CapabilityReport | None, purpose: Purpose, effort: Effort, tier: Tier = "default"
) -> list[str]:
    """Why a probed binding cannot serve a purpose; unverified items pass and stay recorded."""
    if report is None:
        return ["not probed yet"]
    reasons = []
    tiers = () if tier == "default" else (f"tier:{tier}",)
    for name in (*REQUIRED[purpose], f"effort:{effort}", *tiers):
        item = report.items.get(name, ProbeItem(status="not_probed"))
        if item.status in {"unsupported", "failed", "not_probed"}:
            detail = f" ({item.detail})" if item.detail else ""
            reasons.append(f"{item_label(name)}: {LABELS[item.status]}{detail}")
    return reasons


FAILURE_TEXT = {
    "auth": "the key is wrong or missing",
    "quota": "out of quota or rate-limited",
    "timeout": "timed out",
    "unavailable": "the model or service is not available",
    "refused": "the model refused",
    "incomplete": "the reply was incomplete",
    "invalid_output": "the reply did not fit the format",
}


class ProbeAnswer(BaseModel):
    verdict: Literal["correct", "incorrect", "unclear"]
    evidence: str
    response: str


class ColorAnswer(BaseModel):
    color: str


PROBE = ModelRequest(
    "classroom_judgment",
    "This is a synthetic technical probe, not a child classroom. "
    "Judge only the given source. Propose one short response; do not change state.",
    {
        "source": "Mia has a toy car. She wants a bridge for her car.",
        "question": "What does Mia want to make?",
        "answer": "a bridge.",
    },
    ProbeAnswer,
    deadline_seconds=60,
)


def solid_png(rgb: tuple[int, int, int] = (255, 0, 0), size: int = 64) -> bytes:
    """A one-colour PNG: the image-input probe and the simulated drawing."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    rows = (b"\x00" + bytes(rgb) * size) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
