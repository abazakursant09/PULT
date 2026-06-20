"""ExecutionLog decision provenance — execution_logs.decision_id

Additive: one nullable column linking an execution to the Decision it applied
(provenance only). Insight-driven executions keep it null — fully backward
compatible, no backfill. This slice does NOT apply decisions; it only lets
ExecutionLog carry Decision provenance.

Revision ID: el1a2b3c4d01
Revises: do1a2b3c4d01
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "el1a2b3c4d01"
down_revision: Union[str, None] = "do1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Soft provenance reference, no hard FK (mirrors insight_key) — keeps the
    # ALTER SQLite-safe (no constraint ALTER) and Postgres-compatible.
    op.add_column(
        "execution_logs",
        sa.Column("decision_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_execlog_decision", "execution_logs", ["decision_id"])


def downgrade() -> None:
    op.drop_index("ix_execlog_decision", table_name="execution_logs")
    op.drop_column("execution_logs", "decision_id")
