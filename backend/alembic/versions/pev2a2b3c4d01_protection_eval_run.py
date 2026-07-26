"""PULT-LAUNCH-2.5B — ProtectionEvaluation: evaluation_run_id (idempotency) + latest index

Additive. Adds a nullable evaluation_run_id, a partial UNIQUE over
(policy_id, product_id, evaluation_run_id) WHERE evaluation_run_id IS NOT NULL so a repeated run for
the same (policy, product) cannot create a duplicate row (a store-wide policy shares one run_id
across its products because product_id is part of the key), and an index for the latest-Evaluation
lookup. Rows written before 2.5B keep evaluation_run_id NULL — no backfill. downgrade removes only
the column and the two new indexes; existing evaluation data survives.

Revision ID: pev2a2b3c4d01
Revises: pev1a2b3c4d01
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pev2a2b3c4d01"
down_revision: Union[str, None] = "pev1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "protection_evaluations"


def upgrade() -> None:
    with op.batch_alter_table(_T, schema=None) as b:
        b.add_column(sa.Column("evaluation_run_id", sa.String(length=36), nullable=True))
    op.create_index("uq_evaluation_run", _T, ["policy_id", "product_id", "evaluation_run_id"],
                    unique=True,
                    sqlite_where=sa.text("evaluation_run_id IS NOT NULL"),
                    postgresql_where=sa.text("evaluation_run_id IS NOT NULL"))
    op.create_index("ix_evaluation_latest", _T, ["policy_id", "product_id", "evaluated_at"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_latest", table_name=_T)
    op.drop_index("uq_evaluation_run", table_name=_T)
    with op.batch_alter_table(_T, schema=None) as b:
        b.drop_column("evaluation_run_id")
