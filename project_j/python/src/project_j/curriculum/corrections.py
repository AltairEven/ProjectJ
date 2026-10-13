from sqlalchemy import select

from project_j.application.documents import document, identity
from project_j.contracts import RevisionRef
from project_j.platform.storage import Publication

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
                "limitations": ["保留旧内容、原答和解释；不重放课堂。"],
            },
            refs=(target,),
        )
        issue = app.save_step(
            operation,
            learner_id,
            "issue",
            [obj],
            publications=(Publication(ref=obj.ref, reason="有版本和主责的更正问题"),),
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
        obj = document(
            "restriction",
            old.object_id,
            operation,
            learner_id,
            old.body | {"restriction_state": "resolved", "recheck_evidence": evidence},
            refs=old.dependency_refs,
            sources=(old.ref,),
            revision=old.revision + 1,
        )
        saved = app.save_step(
            operation,
            learner_id,
            "release",
            [obj],
            publications=(
                Publication(
                    ref=obj.ref, expected_revision=old.revision, reason="主责针对确切版本复核解除"
                ),
            ),
        )
    return app.ledger.finish(
        operation, learner_id, {"restriction_ref": saved[0].ref.model_dump(), "state": "resolved"}
    )
