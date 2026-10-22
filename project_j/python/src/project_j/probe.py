"""Offline self-check of registered model adapters; no private configuration or network."""

import json

from project_j.platform.models.registry import ADAPTERS


def preflight() -> dict:
    return {
        "mode": "offline_preflight",
        "adapters": [
            {
                "adapter": adapter_id,
                "version": adapter.version,
                "efforts": list(adapter.efforts),
                "simulated": adapter.simulated,
            }
            for adapter_id, adapter in ADAPTERS.items()
        ],
        "live_probe": "project-j-init probe",
        "actual_api": "unverified",
        "actual_voice": "unverified",
    }


def main() -> int:
    """Offline only; real probing runs through project-j-init."""
    print(json.dumps(preflight(), ensure_ascii=False, indent=2))
    return 0
