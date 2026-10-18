"""Partial executable mapping of design/shared/business-contracts.md (P0)."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RevisionRef(Contract):
    object_id: str = Field(min_length=1)
    revision: int = Field(ge=1, strict=True)


OWNERS = {
    "state": "learning",
    "plan": "learning",
    "demand": "learning",
    "household": "learning",
    "review": "learning",
    "decision": "learning",
    "capability": "learning",
    "preview": "preparation",
    "content_check": "preparation",
    "issue": "evaluation",
    "restriction": "curriculum",
    "jane": "teacher",
    "course": "curriculum",
    "brief": "curriculum",
    "material": "curriculum",
    "config": "platform",
    "input_policy": "interface",
    "validation": "application",
    "start": "classroom",
    "activity": "classroom",
    "fact": "evaluation",
    "feedback": "evaluation",
    "parent_report": "evaluation",
    "evaluation": "evaluation",
    "suggestion": "evaluation",
    "report": "evaluation",
}
PERSONAL = {
    "state",
    "plan",
    "demand",
    "start",
    "activity",
    "fact",
    "feedback",
    "parent_report",
    "evaluation",
    "suggestion",
    "report",
    "course",
    "brief",
    "review",
    "decision",
    "preview",
    "content_check",
    "issue",
    "restriction",
}


class ObjectEnvelope(Contract):
    schema_version: Literal[1] = 1
    object_id: str = Field(min_length=1)
    revision: int = Field(ge=1, strict=True)
    kind: str
    owner: str
    realm: Literal["simulation", "adult_check", "family"]
    source_type: Literal["simulated", "adult", "technical", "child", "external_real"]
    learner_id: str | None
    scope: str = Field(min_length=1)
    occurred_at: AwareDatetime | None
    received_at: AwareDatetime | None = None
    created_at: AwareDatetime
    source_revision: int = Field(ge=1, strict=True)
    operation_id: str = Field(min_length=1)
    dependency_refs: tuple[RevisionRef, ...] = ()
    source_refs: tuple[RevisionRef, ...] = ()
    limitations: tuple[str, ...] = ()
    supersedes: RevisionRef | None = None

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        if self.kind not in OWNERS or self.owner != OWNERS[self.kind]:
            raise ValueError("unknown kind or wrong business owner")
        if self.kind in PERSONAL and not self.learner_id:
            raise ValueError("personal object needs learner_id")
        if self.realm == "simulation" and self.source_type != "simulated":
            raise ValueError("simulation cannot relabel a real source")
        if self.realm == "adult_check" and self.source_type not in {"adult", "technical"}:
            raise ValueError("adult check cannot contain child sources")
        if self.realm == "family" and self.source_type in {"adult", "technical", "simulated"}:
            raise ValueError("development sources cannot enter family objects")
        if self.occurred_at is None and not self.limitations:
            raise ValueError("unknown occurrence time needs a limitation")
        return self

    @property
    def ref(self) -> RevisionRef:
        return RevisionRef(object_id=self.object_id, revision=self.revision)


class Document(ObjectEnvelope):
    """Physical storage envelope; business modules validate their own body contracts."""

    realm: Literal["simulation"] = "simulation"
    source_type: Literal["simulated"] = "simulated"
    body: dict[str, JsonValue]

    @model_validator(mode="after")
    def previous_revision(self) -> Self:
        expected = (
            RevisionRef(object_id=self.object_id, revision=self.revision - 1)
            if self.revision > 1
            else None
        )
        if self.supersedes != expected:
            raise ValueError("a revision must retain its exact predecessor")
        return self

    @property
    def references(self) -> tuple[RevisionRef, ...]:
        return (
            self.dependency_refs
            + self.source_refs
            + ((self.supersedes,) if self.supersedes else ())
        )


def validate_e2e01_reference_fixture(objects: list[ObjectEnvelope]) -> None:
    """Only identity/reference validation, not execution or business-body validation."""
    index = {(x.object_id, x.revision): x for x in objects}
    if len(index) != len(objects):
        raise ValueError("duplicate immutable revision")
    for obj in objects:
        for ref in obj.dependency_refs + obj.source_refs:
            target = index.get((ref.object_id, ref.revision))
            if target is None:
                raise ValueError(f"missing exact revision: {obj.object_id}->{ref.object_id}")
            if target.learner_id and target.learner_id != obj.learner_id:
                raise ValueError("cross-learner reference")
            if target.learner_id and target.realm != obj.realm:
                raise ValueError("cross-realm personal reference")
    # E2E01 fixture must reach an actual course node, not stop at a suggestion.
    by_id = {x.object_id: x for x in objects}
    if len(by_id) != len(objects):
        raise ValueError("fixture must select one exact revision for each identity")
    required_kinds = {
        "C1": "course",
        "C2": "course",
        "D1": "demand",
        "D2": "demand",
        "S2": "state",
        "PL1": "plan",
        "E1": "evaluation",
        "N1": "suggestion",
        "F1": "fact",
        "FB1": "feedback",
    }
    for identity, kind in required_kinds.items():
        if identity not in by_id or by_id[identity].kind != kind:
            raise ValueError(f"E2E01 fixture missing or wrongly typed {identity}")
    for child, parent in [
        ("C1", "D1"),
        ("E1", "F1"),
        ("E1", "FB1"),
        ("D2", "S2"),
        ("D2", "PL1"),
        ("D2", "N1"),
        ("C2", "D2"),
    ]:
        if child not in by_id or parent not in by_id:
            raise ValueError(f"E2E01 reference fixture missing {child}/{parent}")
        if by_id[parent].ref not in by_id[child].dependency_refs + by_id[child].source_refs:
            raise ValueError(f"missing required link {child}->{parent}")


class SubmittedInput(Contract):
    event_id: str = Field(min_length=1)
    activity_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    task_instance_id: str = Field(min_length=1)
    wait_id: str = Field(min_length=1)
    expected_core_revision: int = Field(ge=1, strict=True)
    cancellation_epoch: int = Field(ge=0, strict=True)
    client_sequence: int = Field(ge=1, strict=True)
    input_policy_ref: RevisionRef
    realm: Literal["simulation", "adult_check"]  # P0 has no family input entry.
    source_type: Literal["simulated", "adult", "technical"]
    # Answers are typed or clicked; only an English reading segment is recorded (ADR-0014).
    modality: Literal["text", "audio", "action_report"]
    # Audio bytes never travel inside this contract; they reach the core in memory beside it.
    text: str | None = Field(default=None, min_length=1)
    provider: str = Field(min_length=1)  # "unknown" is explicit, never inferred child.
    intent: Literal["answer", "help", "confirmation", "report", "unknown"]
    completion_trigger: Literal["explicit", "max_duration"]
    input_started_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_ended_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    recording_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pause_count: int = Field(default=0, ge=0, strict=True)
    relation: Literal["new", "supplement"] = "new"
    related_event_id: str | None = None

    @model_validator(mode="after")
    def check_submission(self) -> Self:
        if self.realm == "simulation" and self.source_type != "simulated":
            raise ValueError("simulation source mismatch")
        if self.realm == "adult_check" and self.source_type == "simulated":
            raise ValueError("adult check source mismatch")
        if self.relation != "new" and not self.related_event_id:
            raise ValueError("a supplement needs its original event")
        if self.related_event_id == self.event_id:
            raise ValueError("a supplement must have a new event identity")
        timing = (self.input_started_ms, self.input_ended_ms, self.recording_ms)
        if self.modality == "audio":
            if self.text is not None or None in timing:
                raise ValueError("a reading recording carries its timing and no text")
            if self.input_ended_ms < self.input_started_ms:
                raise ValueError("a recording cannot end before it starts")
            if self.relation != "new":
                raise ValueError("a reread is a new attempt, never a supplement")
        else:
            if self.text is None:
                raise ValueError("a typed or reported input needs its text")
            if self.completion_trigger != "explicit" or any(x is not None for x in timing):
                raise ValueError("only a reading recording ends by time or carries timing")
        return self
