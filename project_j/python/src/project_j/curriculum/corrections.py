import logging

from sqlalchemy import select

from project_j.application.documents import document, identity, publication, successor
from project_j.contracts import RevisionRef

log = logging.getLogger(__name__)

# Facts and source reports stay historical truth; restriction records and issues are not uses.
NOT_RESTRICTED = {"fact", "feedback", "parent_report", "restriction", "issue"}


def active_restrictions(app, learner_id: str, selected_refs):
    # Check selected versions; superseded history is not a dependency to be used again.
    selected = set(selected_refs)
    return [
        doc
        for doc in app.store.list_current(learner_id, "restriction")
        if doc.body.get("restriction_state") == "active"
        and selected.intersection(doc.dependency_refs)
    ]


def affected(app, target: RevisionRef, learner_id: str):
    app.store.ensure_idle()
    table = app.store.tables["references"]
    pending, seen, result = [target], {target}, []
    with app.store.engine.begin() as connection:
        while pending:
            ref = pending.pop()
            rows = connection.execute(
                select(table.c.object_id, table.c.revision).where(
                    table.c.target_id == ref.object_id, table.c.target_revision == ref.revision
                )
            )
            for object_id, revision in rows:
                candidate = RevisionRef(object_id=object_id, revision=revision)
                if candidate in seen:
                    continue
                seen.add(candidate)
                doc = app.store.load(connection, candidate, None)
                if doc.learner_id not in {None, learner_id} or doc.kind in NOT_RESTRICTED:
                    continue
                result.append(doc)
                pending.append(candidate)
    return result


def restrict_affected(app, operation_id: str, learner_id: str, targets, reason: str):
    docs = {doc.ref: doc for doc in targets}
    for target in targets:
        docs |= {doc.ref: doc for doc in affected(app, target.ref, learner_id)}
    return [
        app.restrict(operation_id, learner_id, doc, reason).ref.model_dump()
        for doc in docs.values()
        if doc.learner_id is not None and doc.kind not in NOT_RESTRICTED
    ]


def resolved(old, operation_id: str, evidence: str, sources):
    """The next version of a restriction, released by its owner with the recheck evidence."""
    return successor(
        old,
        operation_id,
        old.body | {"restriction_state": "resolved", "recheck_evidence": evidence},
        refs=old.dependency_refs,
        sources=sources,
    )


def correction_restrictions(app, learner_id: str, activity_id: str):
    """Active restrictions a correction of this activity raised: on versions still in use, and
    on versions a newer one already replaced (those stay restricted as history)."""
    raised_by = {
        doc.operation_id
        for doc in app.feedbacks(activity_id, learner_id)
        if doc.body["corrections"]
    }
    in_use, replaced = [], []
    for doc in app.store.list_current(learner_id, "restriction"):
        if doc.body["restriction_state"] != "active" or doc.operation_id not in raised_by:
            continue
        target = RevisionRef.model_validate(doc.body["target_ref"])
        current = app.store.current(target.object_id, learner_id)
        (in_use if current is not None and current.ref == target else replaced).append(doc)
    return in_use, replaced


def release_rechecked(app, operation_id: str, learner_id: str, evaluation) -> None:
    """Evaluation and learning rechecked a corrected activity with its new evaluation; the
    versions still in use are released with that evidence (the owner releases its own)."""
    activity_id = evaluation.body["activity_id"]
    in_use, replaced = correction_restrictions(app, learner_id, activity_id)
    if not in_use and not replaced:
        return
    if in_use:
        evidence = (
            f"Checked against corrected evaluation version {evaluation.revision}; "
            "versions in use stay."
        )
        objects = [
            resolved(doc, operation_id, evidence, (doc.ref, evaluation.ref)) for doc in in_use
        ]
        app.save_step(
            operation_id,
            learner_id,
            "release",
            objects,
            publications=tuple(
                publication(obj, "Released after review against the correction") for obj in objects
            ),
        )
    log.info(
        "correction rechecked activity=%s released=%d kept=%d",
        activity_id,
        len(in_use),
        len(replaced),
    )


def correct(app, request_id: str, learner_id: str, target: RevisionRef, note: str, owner: str):
    original = app.store.read(target, learner_id)
    if owner != original.owner:
        raise ValueError("only the actual business owner can maintain the correction")
    operation = app.request(
        request_id,
        learner_id,
        "content-correction",
        {"target": target.model_dump(), "note": note, "owner": owner},
        sources=(target,),
    )
    result = app.ledger.result(operation, learner_id)
    if result:
        return result
    issue = app.saved_step(operation, learner_id, "issue")
    if issue is None:
        obj = document(
            "issue",
            identity(operation, "issue"),
            operation,
            learner_id,
            {
                "status": "checked",
                "target_ref": target.model_dump(),
                "original_owner": owner,
                "note": note,
                "state": "pending_verification",
                "limitations": [
                    "Old content, answers and readings stay; the class is not replayed."
                ],
            },
            refs=(target,),
        )
        issue = app.save_step(
            operation,
            learner_id,
            "issue",
            [obj],
            publications=(publication(obj, "Correction issue with an exact version and an owner"),),
        )
    impacted = affected(app, target, learner_id)
    restrictions = restrict_affected(app, operation, learner_id, [original], note)
    return app.ledger.finish(
        operation,
        learner_id,
        {
            "issue_ref": issue[0].ref.model_dump(),
            "restriction_refs": restrictions,
            "affected_refs": [doc.ref.model_dump() for doc in impacted],
            "next_step": "owner_recheck",
        },
    )


def release(
    app, request_id: str, learner_id: str, restriction_ref: RevisionRef, owner: str, evidence: str
):
    old = app.store.read(restriction_ref, learner_id)
    target = app.store.read(RevisionRef.model_validate(old.body["target_ref"]), learner_id)
    if owner != target.owner or not evidence.strip():
        raise ValueError("release requires the responsible owner's concrete recheck evidence")
    operation = app.request(
        request_id,
        learner_id,
        "release-restriction",
        {"ref": restriction_ref.model_dump(), "owner": owner, "evidence": evidence},
        sources=(restriction_ref, target.ref),
    )
    result = app.ledger.result(operation, learner_id)
    if result:
        return result
    saved = app.saved_step(operation, learner_id, "release")
    if saved is None:
        obj = resolved(old, operation, evidence, (old.ref,))
        saved = app.save_step(
            operation,
            learner_id,
            "release",
            [obj],
            publications=(
                publication(obj, "Released by the owner after reviewing the exact version"),
            ),
        )
    return app.ledger.finish(
        operation, learner_id, {"restriction_ref": saved[0].ref.model_dump(), "state": "resolved"}
    )
