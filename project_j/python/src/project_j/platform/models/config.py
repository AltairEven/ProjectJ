"""Local model configuration: readable bindings and the init record; no keys."""

import hashlib
import json
from pathlib import Path

from project_j.platform.files import private_write
from project_j.platform.models.contracts import PURPOSES, Binding, Purpose


class ModelFiles:
    def __init__(self, root: Path):
        self.root = root
        if root.is_symlink():
            raise ValueError("model configuration paths must not be symlinks")

    def path(self, relative: str) -> Path:
        path = self.root / relative
        if path.is_symlink():
            raise ValueError("model configuration files must not be symlinks")
        return path

    def read(self, relative: str) -> dict | None:
        path = self.path(relative)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise ValueError(f"配置文件无法读取：{relative}") from error

    def write(self, relative: str, data: dict):
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        private_write(self.path(relative), content.encode())
        self.root.chmod(0o700)

    def bindings(self) -> dict[Purpose, Binding] | None:
        data = self.read("config.json")
        if data is None:
            return None
        try:
            default = Binding.model_validate(data["default"])
            overrides = data.get("purposes", {})
            return {
                purpose: Binding.model_validate(overrides.get(purpose, default.model_dump()))
                for purpose in PURPOSES
            }
        except (KeyError, ValueError) as error:
            raise ValueError("配置文件无法读取：config.json") from error

    def configure(self, default: Binding, purposes: dict[Purpose, Binding]):
        self.write(
            "config.json",
            {
                "default": default.model_dump(),
                "purposes": {
                    purpose: binding.model_dump() for purpose, binding in purposes.items()
                },
            },
        )

    def digest(self, versions: dict[str, str]) -> str:
        """Bindings and adapter versions; "keys" stays empty so records made while an API-key
        adapter existed (until 2026-10-21) remain valid."""
        bindings = self.bindings() or {}
        material = {
            "bindings": {purpose: b.model_dump() for purpose, b in bindings.items()},
            "versions": versions,
            "keys": {},
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def init_record(self) -> dict | None:
        return self.read("init.json")

    def save_init(self, record: dict):
        self.write("init.json", record)
