from pathlib import Path

import pytest

from project_j.application.coordinator import Coordinator
from project_j.learning.fixtures import initialize
from project_j.platform.storage import SimulationStore

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def application(tmp_path):
    store = SimulationStore(tmp_path / "simulation", "run1")
    app = Coordinator(
        store,
        ROOT,
        verification={
            "result": "pass",
            "method": "explicit_test_fixture",
            "scope": "simulation_only",
            "source_type": "simulated",
            "qualification": "not_granted",
        },
    )
    initialize(app)
    try:
        yield app
    finally:
        store.close()
