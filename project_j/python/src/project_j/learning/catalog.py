"""First-course capability identities; aliases resolve within an explicit subject scope."""

from typing import Literal

from pydantic import Field, model_validator

from project_j.application.documents import document, identity, publication
from project_j.contracts import Contract, RevisionRef
from project_j.platform.storage import Conflict


class Capability(Contract):
    status: Literal["checked"] = "checked"
    dimension_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    split_from: RevisionRef | None = None
    limitations: tuple[str, ...] = ("A definition is not an observation or mastery.",)

    @model_validator(mode="after")
    def names(self):
        values = [self.name, *self.aliases]
        normalized = [value.strip().casefold() for value in values]
        if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("capability names and aliases must be distinct and nonempty")
        return self


INITIAL = (
    Capability(
        dimension_id="english.reading.facts",
        subject="english.reading",
        name="Reading facts and order",
        definition="Find facts, their order and the words that show them in a fixed text.",
        aliases=("Story order",),
    ),
    Capability(
        dimension_id="english.speaking.report",
        subject="english.speaking",
        name="Short English reports",
        definition="Report what really happened; help and language are kept apart.",
    ),
    Capability(
        dimension_id="english.speaking.pronunciation",
        subject="english.speaking",
        name="Reading aloud",
        definition="Pronunciation when reading a text aloud, from the pronunciation score; "
        "the model does not judge it again.",
    ),
    Capability(
        dimension_id="science.observation.report",
        subject="science",
        name="Real observation reports",
        definition="Give the real conditions, change, result and source; never infer "
        "real things from the story.",
    ),
)


def resolve(app, learner_id: str, subject: str, name: str):
    normalized = name.strip().casefold()
    matches = []
    for doc in app.store.list_current(learner_id, "capability"):
        body = Capability.model_validate(doc.body)
        if body.subject == subject and normalized in {
            value.strip().casefold() for value in (body.name, *body.aliases)
        }:
            matches.append(doc)
    if len(matches) > 1:
        raise Conflict("ambiguous capability identity needs learning review")
    return matches[0] if matches else None


def define(app, request_id: str, learner_id: str, body: Capability):
    completed = app.ledger.completed_request(
        request_id, learner_id, "capability-definition", body.model_dump(mode="json")
    )
    if completed:
        return completed
    for name in (body.name, *body.aliases):
        existing = resolve(app, learner_id, body.subject, name)
        if existing and existing.body["dimension_id"] != body.dimension_id:
            raise Conflict("same scoped name must reuse its established capability identity")
    object_id = identity("shared", f"capability:{body.dimension_id}")
    existing = app.store.current(object_id, learner_id)
    if existing:
        if Capability.model_validate(existing.body) == body:
            return {"capability_ref": existing.ref.model_dump(), "effect": "reused"}
        raise Conflict(
            "definition revision needs an explicit impact review; original remains valid"
        )
    operation = app.request(
        request_id, learner_id, "capability-definition", body.model_dump(mode="json")
    )
    saved = app.saved_step(operation, learner_id, "capability")
    if saved is None:
        if body.split_from:
            parent = app.store.read(body.split_from, learner_id)
            if parent.kind != "capability":
                raise ValueError("a split refers to an exact capability definition")
        obj = document(
            "capability",
            object_id,
            operation,
            None,
            body,
            sources=(body.split_from,) if body.split_from else (),
        )
        saved = app.save_step(
            operation,
            learner_id,
            "capability",
            [obj],
            publications=(publication(obj, "Simulated catalog definitions with a stated scope"),),
        )
    return app.ledger.finish(
        operation,
        learner_id,
        {"capability_ref": saved[0].ref.model_dump(), "effect": "catalog_only"},
    )
