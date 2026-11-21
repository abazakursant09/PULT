"""Dormant DRAFT queue expansion. No backfill, dispatch or user alteration.

Application rollback retains the table AND this Alembic revision. Code lacking
this revision must be deployed with AUTO_MIGRATE=0, or the migration-aware code
must remain; default upgrade-head startup cannot understand a newer revision.
Do not stamp backwards. Explicit schema contraction refuses any row, including
DRAFT: neither age nor lack of a local ACK permits evidence loss.
"""
import sqlalchemy as sa
from alembic import op

revision = "ejp1a2b3c4d01"
down_revision = "acl1a2b3c4d01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "erasure_preparations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("closure_policy_version", sa.String(32), nullable=False),
        sa.Column("closure_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", name="uq_erasure_preparations_user"),
        sa.CheckConstraint("state = 'DRAFT'", name="ck_erasure_preparations_draft_only"),
        sa.CheckConstraint("length(id) = 36", name="ck_erasure_preparations_id_length"),
    )


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Cannot inspect erasure preparation evidence in offline downgrade")
    bind = op.get_bind()
    table = sa.table("erasure_preparations", sa.column("state"))
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE erasure_preparations IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.update(table).values(state=table.c.state))
    else:
        raise RuntimeError("Unsupported erasure preparation downgrade dialect")
    if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
        raise RuntimeError("Erasure preparation evidence exists; retain table")
    op.drop_table("erasure_preparations")
