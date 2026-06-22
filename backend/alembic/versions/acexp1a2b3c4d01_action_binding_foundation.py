"""Action Catalog Expansion data foundation (A2) — action_binding_audit

Additive, non-destructive, no backfill, no touch to existing tables. One append-only
ledger recording, per signal type, which catalog action was bound (if any) and why.
Marketplace-agnostic, soft refs. SQLite-safe (CREATE TABLE + CREATE INDEX) and
Postgres-compatible. No execution, no bridge, no API — schema only.

Revision ID: acexp1a2b3c4d01
Revises: dfeed1a2b3c4d01
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "acexp1a2b3c4d01"
down_revision: Union[str, None] = "dfeed1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "action_binding_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=True),
        sa.Column("binding_status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_action_binding_audit_user", "action_binding_audit", ["user_id"])
    op.create_index("ix_action_binding_audit_signal", "action_binding_audit", ["signal_type"])
    op.create_index("ix_action_binding_audit_status", "action_binding_audit", ["binding_status"])


def downgrade() -> None:
    op.drop_index("ix_action_binding_audit_status", table_name="action_binding_audit")
    op.drop_index("ix_action_binding_audit_signal", table_name="action_binding_audit")
    op.drop_index("ix_action_binding_audit_user", table_name="action_binding_audit")
    op.drop_table("action_binding_audit")
