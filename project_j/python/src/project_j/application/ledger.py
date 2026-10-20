"""Persistent request identities and per-source step results outside classroom time."""

import json
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, JsonValue
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from project_j.contracts import Contract, RevisionRef
from project_j.platform.storage import Conflict, SimulationStore, canonical, digest


class Request(Contract):
    realm: Literal["simulation", "study"] = "simulation"
    source_type: Literal["simulated", "student"] = "simulated"
    caller: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    source_revision: int = Field(default=1, ge=1, strict=True)
    source_refs: tuple[RevisionRef, ...] = ()
    expected_baselines: tuple[RevisionRef, ...] = ()
    body: dict[str, JsonValue]


class Step(Contract):
    step_id: str = Field(min_length=1)
    source_revision: int = Field(ge=1, strict=True)
    owner: str = Field(min_length=1)
    input_refs: tuple[RevisionRef, ...] = ()
    input_digest: str = Field(min_length=1)
    attempts: int = Field(ge=0, le=3, strict=True)
    deadline: AwareDatetime | None = None
    execution: Literal[
        "accepted", "running", "waiting", "partial", "failed", "cancelled", "complete"
    ]
    generation: Literal["not_requested", "generated", "failed"] = "not_requested"
    save: Literal["not_requested", "succeeded", "failed", "unknown"] = "not_requested"
    result_refs: tuple[RevisionRef, ...] = ()
    effect_refs: tuple[RevisionRef, ...] = ()
    next_step: str | None = None
    failure: str | None = None
    required_input: tuple[str, ...] = ()


class Ledger:
    def __init__(self, store: SimulationStore):
        self.store = store

    def completed_request(self, request_id: str, learner_id: str, purpose: str, body: dict):
        self.store.ensure_idle()
        table = self.store.tables["operations"]
        with self.store.engine.begin() as connection:
            row = (
                connection.execute(
                    select(table).where(
                        table.c.caller == "simulation_parent", table.c.request_id == request_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        request = Request.model_validate_json(row["request"])
        if (
            request.learner_id != learner_id
            or request.purpose != purpose
            or any(request.body.get(key) != value for key, value in body.items())
        ):
            raise Conflict("same request identity has different content, purpose or learner")
        return json.loads(row["result"]) if row["result"] else None

    def done(self, request_id: str) -> bool:
        """Whether the operation of this request identity finished with a result."""
        self.store.ensure_idle()
        table = self.store.tables["operations"]
        with self.store.engine.begin() as connection:
            row = connection.execute(
                select(table.c.result).where(
                    table.c.caller == "simulation_parent", table.c.request_id == request_id
                )
            ).one_or_none()
        return row is not None and row.result is not None

    def accept(self, request: Request, planned_operation_id: str | None = None) -> str:
        table = self.store.tables["operations"]
        request = request.model_copy(
            update={"realm": self.store.realm, "source_type": self.store.source_type}
        )
        payload = canonical(request)
        with self.store.write() as connection:
            row = (
                connection.execute(
                    select(table).where(
                        table.c.caller == request.caller,
                        table.c.request_id == request.request_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row:
                if row["payload_digest"] != digest(payload):
                    raise Conflict(
                        "same request identity has different content, purpose or learner"
                    )
                return row["operation_id"]
            for ref in request.source_refs + request.expected_baselines:
                self.store.load(connection, ref, request.learner_id)
            operation_id = planned_operation_id or str(uuid4())
            connection.execute(
                table.insert().values(
                    operation_id=operation_id,
                    caller=request.caller,
                    request_id=request.request_id,
                    learner_id=request.learner_id,
                    purpose=request.purpose,
                    payload_digest=digest(payload),
                    request=payload,
                )
            )
            return operation_id

    def operation(self, operation_id: str, learner_id: str):
        self.store.ensure_idle()
        table = self.store.tables["operations"]
        with self.store.engine.begin() as connection:
            row = (
                connection.execute(select(table).where(table.c.operation_id == operation_id))
                .mappings()
                .one_or_none()
            )
            if row is None or row["learner_id"] != learner_id:
                raise LookupError("operation unavailable for this learner")
            return dict(row)

    def result(self, operation_id: str, learner_id: str):
        value = self.operation(operation_id, learner_id)["result"]
        return json.loads(value) if value else None

    def finish(self, operation_id: str, learner_id: str, result: dict):
        self.operation(operation_id, learner_id)
        table = self.store.tables["operations"]
        payload = canonical(result)
        with self.store.write() as connection:
            current = connection.execute(
                select(table.c.result).where(table.c.operation_id == operation_id)
            ).scalar_one()
            if current is not None:
                if current != payload:
                    raise Conflict("completed operation result is immutable")
                return json.loads(current)
            self.store.fault("ledger")
            connection.execute(
                update(table)
                .where(table.c.operation_id == operation_id, table.c.result.is_(None))
                .values(result=payload)
            )
        return result

    def checkpoint(
        self,
        operation_id: str,
        learner_id: str,
        step: Step,
        expected_version=None,
        replace_unfinished=False,
    ):
        self.operation(operation_id, learner_id)
        table = self.store.tables["steps"]
        with self.store.write() as connection:
            condition = (
                table.c.operation_id == operation_id,
                table.c.step_id == step.step_id,
                table.c.source_revision == step.source_revision,
            )
            row = connection.execute(select(table).where(*condition)).mappings().one_or_none()
            if row:
                previous = Step.model_validate_json(row["payload"])
                if previous.execution == "complete":
                    if canonical(previous) != canonical(step):
                        raise Conflict("successful step cannot be run or rewritten again")
                    return previous, row["version"]
                if replace_unfinished:
                    expected_version = row["version"]
                if expected_version != row["version"]:
                    raise Conflict("step progress CAS conflict")
                if (
                    previous.input_digest != step.input_digest
                    or previous.input_refs != step.input_refs
                ):
                    raise Conflict("same-source step inputs cannot change")
                version = row["version"] + 1
                connection.execute(
                    update(table)
                    .where(*condition, table.c.version == expected_version)
                    .values(payload=canonical(step), version=version)
                )
            else:
                if expected_version is not None:
                    raise Conflict("missing step baseline")
                version = 1
                connection.execute(
                    insert(table).values(
                        operation_id=operation_id,
                        step_id=step.step_id,
                        source_revision=step.source_revision,
                        payload=canonical(step),
                        version=version,
                    )
                )
            return step, version

    def steps(self, operation_id: str, learner_id: str):
        self.operation(operation_id, learner_id)
        table = self.store.tables["steps"]
        with self.store.engine.begin() as connection:
            return [
                dict(row) | {"payload": json.loads(row["payload"])}
                for row in connection.execute(
                    select(table).where(table.c.operation_id == operation_id)
                ).mappings()
            ]
