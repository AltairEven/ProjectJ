"""Registered adapters: the simulated one and the Codex subscription (its own login home)."""

from project_j.platform.models.codex_sdk import CodexAdapter
from project_j.platform.models.config import ModelFiles
from project_j.platform.models.contracts import CapabilityReport
from project_j.platform.models.service import ModelService
from project_j.platform.models.simulated import SimulatedAdapter

ADAPTERS = {"simulated": SimulatedAdapter, "codex": CodexAdapter}
NEEDS_LOGIN = {"codex"}  # Subscription login kept in the adapter's own home directory.


def build(adapter_id: str, files: ModelFiles):
    if adapter_id not in ADAPTERS:
        raise ValueError(f"未知适配器：{adapter_id}")
    if adapter_id in NEEDS_LOGIN:
        return ADAPTERS[adapter_id](files.path(f"{adapter_id}-home"))
    return ADAPTERS[adapter_id]()


def versions(bindings) -> dict[str, str]:
    return {
        b.adapter: ADAPTERS[b.adapter].version for b in bindings.values() if b.adapter in ADAPTERS
    }


def load_service(files: ModelFiles) -> ModelService:
    """Real configuration for project-j-init; the phase-1 server never calls this."""
    try:
        bindings = files.bindings()
        if bindings is None:
            return ModelService(
                {}, {}, [], problem="未配置默认模型，请运行 project-j-init configure"
            )
        adapters = {a: build(a, files) for a in {b.adapter for b in bindings.values()}}
        record = files.init_record()
        if record is None:
            return ModelService(
                adapters, bindings, [], problem="尚未探测，请运行 project-j-init probe"
            )
        if record["config_digest"] != files.digest(versions(bindings)):
            return ModelService(
                adapters,
                bindings,
                [],
                problem="配置或适配器版本已改动，请运行 project-j-init probe",
            )
        reports = [CapabilityReport.model_validate(report) for report in record["reports"]]
        return ModelService(adapters, bindings, reports)
    except (KeyError, ValueError) as error:
        return ModelService({}, {}, [], problem=str(error))
