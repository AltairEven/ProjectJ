"""Initial isolated object revisions, references, receipts and operation ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0001_simulation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Each database holds one data space; its rows can carry no other realm or source label.
    realm, source = op.get_context().config.attributes.get("realm", ("simulation", "simulated"))
    if (realm, source) not in {("simulation", "simulated"), ("adult_check", "adult")}:
        raise ValueError("unknown data space")
    op.create_table(
        "identities",
        sa.Column("object_id", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("owner", sa.Text, nullable=False),
        sa.Column("learner_id", sa.Text),
        sa.Column("scope", sa.Text, nullable=False),
    )
    op.create_table(
        "revisions",
        sa.Column("object_id", sa.Text, primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("digest", sa.Text, nullable=False),
        sa.CheckConstraint("revision >= 1"),
        sa.CheckConstraint(f"json_extract(payload, '$.realm') = '{realm}'"),
        sa.CheckConstraint(f"json_extract(payload, '$.source_type') = '{source}'"),
        sa.CheckConstraint("json_extract(payload, '$.object_id') = object_id"),
        sa.CheckConstraint("json_extract(payload, '$.revision') = revision"),
        sa.ForeignKeyConstraint(["object_id"], ["identities.object_id"]),
    )
    op.create_table(
        "references",
        sa.Column("object_id", sa.Text, primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("target_id", sa.Text, primary_key=True),
        sa.Column("target_revision", sa.Integer, primary_key=True),
        sa.ForeignKeyConstraint(
            ["object_id", "revision"],
            ["revisions.object_id", "revisions.revision"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["target_id", "target_revision"],
            ["revisions.object_id", "revisions.revision"],
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_table(
        "effective",
        sa.Column("object_id", sa.Text, primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id", "revision"], ["revisions.object_id", "revisions.revision"]
        ),
    )
    op.create_table(
        "receipts",
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("step_id", sa.Text, primary_key=True),
        sa.Column("source_revision", sa.Integer, primary_key=True),
        sa.Column("learner_id", sa.Text, nullable=False),
        sa.Column("payload_digest", sa.Text, nullable=False),
        sa.Column("result", sa.Text, nullable=False),
        sa.CheckConstraint("source_revision >= 1"),
    )
    op.create_table(
        "operations",
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("caller", sa.Text, nullable=False),
        sa.Column("request_id", sa.Text, nullable=False),
        sa.Column("learner_id", sa.Text, nullable=False),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column("payload_digest", sa.Text, nullable=False),
        sa.Column("request", sa.Text, nullable=False),
        sa.Column("result", sa.Text),
        sa.CheckConstraint(f"json_extract(request, '$.realm') = '{realm}'"),
        sa.CheckConstraint(f"json_extract(request, '$.source_type') = '{source}'"),
        sa.UniqueConstraint("caller", "request_id"),
    )
    op.create_table(
        "steps",
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("step_id", sa.Text, primary_key=True),
        sa.Column("source_revision", sa.Integer, primary_key=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.CheckConstraint("source_revision >= 1 AND version >= 1"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.operation_id"]),
    )
    for table in ("identities", "revisions", "references", "receipts"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f'CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON "{table}" '
                "BEGIN SELECT RAISE(ABORT, 'immutable record'); END"
            )


def downgrade():
    raise RuntimeError("destructive downgrade is not supported; restore to a new isolated root")
