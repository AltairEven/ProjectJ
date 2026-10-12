import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_j.learning.contracts import record_adapter

FIXTURE = Path(__file__).parent / "fixtures/p1a_initial.json"


def initial_records():
    return [record_adapter.validate_python(item) for item in json.loads(FIXTURE.read_text())]


def test_initial_state_and_demand_keep_unknown_dimensions_and_exact_references():
    household, state, plan, demand = initial_records()
    assert household.body.applies_to == (state.learner_id,)
    assert all(
        item.judgment == "unknown" and item.help == "unknown" for item in state.body.dimensions
    )
    assert all(item.retention is None and item.transfer is None for item in state.body.dimensions)
    assert demand.body.state_ref == state.ref
    assert demand.body.plan_ref == plan.ref
    assert demand.body.household_ref == household.ref
    assert demand.body.budget_minutes == 30


@pytest.mark.parametrize(
    "change",
    [
        {"realm": "family", "source_type": "child"},
        {"realm": "adult_check", "source_type": "adult"},
        {"source_type": "technical"},
        {"owner": "platform"},
        {"learner_id": None},
        {"revision": 2},
        {"received_at": "2026-10-12T12:00:00"},
    ],
)
def test_learning_objects_reject_non_simulation_and_incomplete_identity(change):
    state = initial_records()[1].model_dump(mode="json")
    with pytest.raises(ValidationError):
        record_adapter.validate_python(state | change)


@pytest.mark.parametrize(
    "change",
    [
        {"judgment": "single_independent", "evidence_refs": []},
        {"judgment": "unknown", "limitations": []},
        {
            "judgment": "single_independent",
            "evidence_refs": [{"object_id": "sim-evaluation", "revision": 1}],
            "help": "unknown",
        },
    ],
)
def test_unknown_or_helped_performance_cannot_be_relabelled_independent(change):
    state = initial_records()[1].model_dump(mode="json")
    state["body"]["dimensions"][0].update(change)
    with pytest.raises(ValidationError):
        record_adapter.validate_python(state)


def test_revision_retains_predecessor_and_body_does_not_accept_extra_score():
    state = initial_records()[1].model_dump(mode="json")
    revised = record_adapter.validate_python(
        state | {"revision": 2, "supersedes": {"object_id": state["object_id"], "revision": 1}}
    )
    assert revised.supersedes == initial_records()[1].ref
    state["body"]["dimensions"][0]["score"] = 0
    with pytest.raises(ValidationError):
        record_adapter.validate_python(state)


@pytest.mark.parametrize("change", [{"budget_minutes": 31}, {"status": "active"}])
def test_demand_does_not_change_budget_or_claim_activation_in_its_body(change):
    demand = initial_records()[3].model_dump(mode="json")
    demand["body"].update(change)
    with pytest.raises(ValidationError):
        record_adapter.validate_python(demand)
