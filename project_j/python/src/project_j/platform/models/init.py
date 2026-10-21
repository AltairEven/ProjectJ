"""project-j-init: configure model bindings, log in, probe adapters, record admission."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from project_j.platform.models.config import ModelFiles
from project_j.platform.models.contracts import (
    LABELS,
    PURPOSE_LABELS,
    PURPOSES,
    Binding,
    admission,
    item_label,
)
from project_j.platform.models.registry import (
    ADAPTERS,
    NEEDS_LOGIN,
    build,
    load_service,
    versions,
)


def parse_binding(text: str) -> Binding:
    parts = text.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(f"绑定格式应为 适配器:模型:档位[:fast]，收到 {text}")
    adapter, model, effort, tier = (*parts, "default")[:4]
    if adapter not in ADAPTERS:
        raise ValueError(f"未知适配器：{adapter}")
    if effort not in ADAPTERS[adapter].efforts:
        raise ValueError(f"{adapter} 不支持档位 {effort}")
    if tier not in ADAPTERS[adapter].tiers:
        raise ValueError(f"{adapter} 不支持层级 {tier}")
    return Binding(adapter=adapter, model=model, effort=effort, tier=tier)


async def probe_all(files: ModelFiles) -> dict:
    bindings = files.bindings()
    if bindings is None:
        raise ValueError("未配置默认模型，请先运行 project-j-init configure")
    wanted: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for binding in bindings.values():
        efforts, tiers = wanted.setdefault((binding.adapter, binding.model), (set(), set()))
        efforts.add(binding.effort)
        if binding.tier != "default":
            tiers.add(binding.tier)
    reports = []
    for (adapter, model), (efforts, tiers) in wanted.items():
        instance = build(adapter, files)
        try:
            reports.append(
                await instance.probe(model, tuple(sorted(efforts)), tuple(sorted(tiers)))
            )
        finally:
            if hasattr(instance, "aclose"):
                await instance.aclose()
    purposes = {}
    for purpose in PURPOSES:
        binding = bindings[purpose]
        report = next(
            r for r in reports if (r.adapter, r.model) == (binding.adapter, binding.model)
        )
        purposes[purpose] = {
            "binding": binding.model_dump(),
            "reasons": admission(report, purpose, binding.effort, binding.tier),
        }
    record = {
        "config_digest": files.digest(versions(bindings)),
        "captured_at": datetime.now(UTC).isoformat(),
        "initialized": not any(item["reasons"] for item in purposes.values()),
        "reports": [report.model_dump(mode="json") for report in reports],
        "purposes": purposes,
    }
    files.save_init(record)
    return record


def show(status: dict, files: ModelFiles):
    print("模型已初始化。" if status["initialized"] else "模型未初始化。")
    for purpose, item in status["purposes"].items():
        binding = item["binding"]
        tier = binding.get("tier", "default") if binding else "default"
        where = (
            f"{binding['adapter']}:{binding['model']}:{binding['effort']}"
            + ("" if tier == "default" else f":{tier}")
            if binding
            else "未配置"
        )
        reasons = "；".join(item["reasons"])
        print(f"- {PURPOSE_LABELS[purpose]}：{where}" + (f"；未通过：{reasons}" if reasons else ""))
    for report in status["reports"]:
        items = "，".join(
            f"{item_label(name)}={LABELS[i['status']]}" for name, i in report["items"].items()
        )
        print(
            f"  能力报告 {report['adapter']}:{report['model']}（{report['captured_at']}）：{items}"
        )
    for adapter in sorted(NEEDS_LOGIN):
        home = files.path(f"{adapter}-home")
        print(f"  {adapter} 登录：{'已登录' if ADAPTERS[adapter].logged_in(home) else '未登录'}")


async def login(files: ModelFiles, adapter_id: str) -> bool:
    adapter = build(adapter_id, files)
    try:
        return await adapter.login(print)
    finally:
        await adapter.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化模型适配层：配置、登录、探测与准入。")
    parser.add_argument("--root", type=Path, default=Path("project_j/private/models"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login").add_argument("adapter")
    configure = commands.add_parser("configure")
    configure.add_argument("--default", required=True)
    configure.add_argument("--purpose", action="append", default=[])
    commands.add_parser("probe")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    if not Path("project_j").is_dir():
        print("请在项目根目录运行（当前目录下没有 project_j）。")
        return 1
    try:
        files = ModelFiles(args.root)
        if args.command == "login":
            if args.adapter not in NEEDS_LOGIN:
                raise ValueError(f"{args.adapter} 不使用订阅登录")
            if not asyncio.run(login(files, args.adapter)):
                print(f"{args.adapter} 登录未完成。")
                return 1
            print(f"{args.adapter} 已登录；请重新运行 project-j-init probe。")
            return 0
        if args.command == "configure":
            default = parse_binding(args.default)
            purposes = {}
            for item in args.purpose:
                purpose, _, binding = item.partition("=")
                if purpose not in PURPOSES:
                    raise ValueError(f"未知用途：{purpose}")
                purposes[purpose] = parse_binding(binding)
            files.configure(default, purposes)
            print("绑定已保存；请运行 project-j-init probe。")
            return 0
        if args.command == "probe":
            asyncio.run(probe_all(files))
        status = load_service(files).status()
        show(status, files)
        return 0 if status["initialized"] else 1
    except ValueError as error:
        print(str(error))
        return 1
