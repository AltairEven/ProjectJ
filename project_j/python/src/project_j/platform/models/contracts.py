"""Provider-neutral model requests and results; adapters absorb provider differences."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue

from project_j.contracts import Contract

# Workflow order; planning and preparation have bindings and admission before they have callers.
Purpose = Literal["planning", "preparation", "classroom_judgment", "evaluation"]
PURPOSES: tuple[Purpose, ...] = ("planning", "preparation", "classroom_judgment", "evaluation")
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
    "verified": "已核实",
    "unverified": "未核实",
    "unsupported": "不支持",
    "failed": "失败",
    "not_probed": "未探测",
}
PURPOSE_LABELS = {
    "planning": "学习规划",
    "preparation": "备课",
    "classroom_judgment": "课堂判断",
    "evaluation": "课后评价",
}
ITEM_LABELS = {
    "auth": "鉴权",
    "model": "模型",
    "structured_output": "结构化输出",
    "cancellation": "取消",
    "image_input": "图片输入",
    "audio": "音频",
    "tools": "工具关闭",
    "tier:fast": "Fast层级",
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
        return f"档位{name.removeprefix('effort:')}"
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
        return ["尚未探测"]
    reasons = []
    tiers = () if tier == "default" else (f"tier:{tier}",)
    for name in (*REQUIRED[purpose], f"effort:{effort}", *tiers):
        item = report.items.get(name, ProbeItem(status="not_probed"))
        if item.status in {"unsupported", "failed", "not_probed"}:
            detail = f"（{item.detail}）" if item.detail else ""
            reasons.append(f"{item_label(name)}：{LABELS[item.status]}{detail}")
    return reasons
