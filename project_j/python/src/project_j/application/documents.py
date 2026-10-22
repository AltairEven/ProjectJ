from copy import deepcopy
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel

from project_j.contracts import OWNERS, Document, RevisionRef
from project_j.platform.storage import Publication


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
        limitations=(
            "Simulation only; the real API, voice and teaching results are not verified.",
        ),
        body=body.model_dump(mode="json") if isinstance(body, BaseModel) else deepcopy(body),
    )


def successor(prior: Document, operation_id: str, body: dict | BaseModel, **fields) -> Document:
    """The next revision of prior; fields are document()'s refs, sources, source_revision and
    occurred_at (each defaults as in document(), not to prior's)."""
    return document(
        prior.kind,
        prior.object_id,
        operation_id,
        prior.learner_id,
        body,
        revision=prior.revision + 1,
        **fields,
    )


def publication(obj: Document, reason: str) -> Publication:
    """obj becomes effective in place of exactly the revision it supersedes, or as the first."""
    expected = obj.supersedes.revision if obj.supersedes else None
    return Publication(ref=obj.ref, expected_revision=expected, reason=reason)
