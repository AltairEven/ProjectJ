import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from project_j.learning.contracts import record_adapter

FIXTURE = Path(__file__).parent / "fixtures/p1a_initial.json"


def initial_records():
    return [record_adapter.validate_python(item) for item in json.loads(FIXTURE.read_text())]


def test_initial_state_and_demand_keep_unknown_dimensions_and_exact_references():
    profile, state, plan, demand = initial_records()
    assert profile.learner_id == state.learner_id and profile.body.lexile == "500L"
    assert all(
        item.judgment == "unknown" and item.help == "unknown" for item in state.body.dimensions
    )
    assert all(item.retention is None and item.transfer is None for item in state.body.dimensions)
    assert demand.body.state_ref == state.ref
    assert demand.body.plan_ref == plan.ref
    assert demand.body.profile_ref == profile.ref
    assert demand.body.budget_minutes == 30


@pytest.mark.parametrize(
    "change",
    [
        {"realm": "family", "source_type": "child"},
        {"realm": "study", "source_type": "simulated"},
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


def test_learning_objects_accept_the_study_realm():
    state = initial_records()[1].model_dump(mode="json")
    study = record_adapter.validate_python(state | {"realm": "study", "source_type": "student"})
    assert (study.realm, study.source_type) == ("study", "student")


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


def test_skill_focus_is_checked_and_becomes_requirements():
    from project_j.learning.contracts import MAX_FOCUS, SKILLS, DemandBody
    from project_j.preparation.contracts import demand_requirements

    assert len(SKILLS) == 13 and MAX_FOCUS == 3
    assert {group for group, _, _ in SKILLS.values()} == {"词类", "时态", "句型"}
    demand = initial_records()[3].body
    before = demand_requirements(demand)
    assert demand.skill_focus is None and demand.focus_suggestions == ()  # older demands read
    base = demand.model_dump(mode="json")
    for bad in (
        {"skill_focus": ["no_such_skill"]},
        {"skill_focus": ["verbs", "adjectives", "adverbs", "articles"]},
        {"skill_focus": ["verbs"], "skill_note": "x" * 101},
        {"focus_suggestions": [{"skill_id": "verbs", "reason": "r"}] * 3},
        {"focus_suggestions": [{"skill_id": "unknown", "reason": "r"}]},
    ):
        with pytest.raises(ValidationError):
            DemandBody.model_validate(base | bad)
    chosen = DemandBody.model_validate(
        base | {"skill_focus": ["past_simple"], "skill_note": "多练不规则动词"}
    )
    added = demand_requirements(chosen)[len(before) :]
    assert added[0][0] == "skill_focus" and added[0][1].startswith("一般过去时：")
    assert added[1] == ("skill_note", "多练不规则动词")
    assert demand_requirements(DemandBody.model_validate(base | {"skill_focus": []})) == before
