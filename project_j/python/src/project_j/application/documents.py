from copy import deepcopy
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel

from project_j.contracts import OWNERS, Document, RevisionRef


def identity(learner_id: str, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"project-j:simulation:{learner_id}:{label}"))


def document(
    kind: str,
    object_id: str,
    operation_id: str,
    learner_id: str | None,
    body: dict | BaseModel,
    refs=(),
    sources=(),
    revision=1,
    source_revision=1,
    occurred_at=None,
):
    return Document(
        object_id=object_id,
        revision=revision,
        kind=kind,
        owner=OWNERS[kind],
        realm="simulation",
        source_type="simulated",
        learner_id=learner_id,
        scope=f"learner:{learner_id}" if learner_id else "explicit shared simulation content",
        occurred_at=occurred_at,
        received_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        source_revision=source_revision,
        operation_id=operation_id,
        dependency_refs=tuple(refs),
        source_refs=tuple(sources),
        supersedes=RevisionRef(object_id=object_id, revision=revision - 1)
        if revision > 1
        else None,
        limitations=("明确隔离模拟；真实API、声音、儿童及教学效果未验证。",),
        body=body.model_dump(mode="json") if isinstance(body, BaseModel) else deepcopy(body),
    )
