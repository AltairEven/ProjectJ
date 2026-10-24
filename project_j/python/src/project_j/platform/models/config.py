"""Local model configuration: readable bindings and the init record; no keys."""

import hashlib
import json
import logging
from pathlib import Path

from project_j.platform.files import private_write
from project_j.platform.models.contracts import (
    DEADLINE_RANGE,
    DEFAULT_DEADLINES,
    PURPOSES,
    Binding,
    DeadlineKey,
    Purpose,
)

log = logging.getLogger(__name__)


class ModelFiles:
    def __init__(self, root: Path):
        self.root = root
        if root.is_symlink():
            raise ValueError("model configuration paths must not be symlinks")
        self.warned: set[str] = set()  # Deadline keys already reported as invalid.

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
            raise ValueError(f"The setup file cannot be read: {relative}") from error

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

            # Word meanings are short, low-effort requests: unless bound on their own, they
            # follow the classroom judgment binding rather than the (xhigh) default.
            def fallback(purpose):
                follow = "classroom_judgment" if purpose == "vocabulary" else purpose
                return overrides.get(follow, default.model_dump())

            return {
                purpose: Binding.model_validate(overrides.get(purpose, fallback(purpose)))
                for purpose in PURPOSES
            }
        except (KeyError, ValueError) as error:
            raise ValueError("The setup file cannot be read: config.json") from error

    def default(self) -> Binding:
        data = self.read("config.json")
        if data is None or "default" not in data:
            raise ValueError("No default model; run project-j-init configure")
        return Binding.model_validate(data["default"])

    def configure(self, default: Binding, purposes: dict[Purpose, Binding]):
        self.write(
            "config.json",
            (self.read("config.json") or {})
            | {
                "default": default.model_dump(),
                "purposes": {
                    purpose: binding.model_dump() for purpose, binding in purposes.items()
                },
            },
        )

    def deadlines(self) -> dict[DeadlineKey, int]:
        """Seconds each kind of call may wait; a missing or invalid entry is its default."""
        values = dict(DEFAULT_DEADLINES)
        try:
            data = self.read("config.json") or {}
        except ValueError:  # A broken file must not stop a class: defaults, reported once.
            if "config.json" not in self.warned:
                self.warned.add("config.json")
                log.warning("deadline source=default reason=unreadable")
            return values
        section = data.get("deadlines") or {}
        low, high = DEADLINE_RANGE
        for key in DEFAULT_DEADLINES:
            value = section.get(key)
            if value is None:
                continue
            if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                values[key] = value
            elif key not in self.warned:
                self.warned.add(key)
                log.warning("deadline key=%s source=default reason=invalid", key)
        return values

    def configure_deadlines(self, values: dict[DeadlineKey, int]):
        self.write("config.json", (self.read("config.json") or {}) | {"deadlines": dict(values)})

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
