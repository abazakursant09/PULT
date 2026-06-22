"""Decision Apply UX data foundation (A2) — decision_apply_intent

Additive, non-destructive, no backfill, no touch to existing tables. One append-only
ledger of seller apply intents (previewed/confirmed/rejected) — foundation for the
future "Применить решение" UX. Marketplace-agnostic, soft refs. SQLite-safe
(CREATE TABLE + CREATE INDEX) and Postgres-compatible. No execution, no API — schema
only.

Revision ID: dauxp1a2b3c4d01
Revises: acexp1a2b3c4d01
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "dauxp1a2b3c4d01"
down_revision: Union[str, None] = "acexp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_apply_intent",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=True),
        sa.Column("intent_status", sa.String(length=15), nullable=False),
        sa.Column("dry_run_status", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_decision_apply_intent_user", "decision_apply_intent", ["user_id"])
    op.create_index("ix_decision_apply_intent_decision", "decision_apply_intent", ["decision_id"])
    op.create_index("ix_decision_apply_intent_status", "decision_apply_intent", ["intent_status"])


def downgrade() -> None:
    op.drop_index("ix_decision_apply_intent_status", table_name="decision_apply_intent")
    op.drop_index("ix_decision_apply_intent_decision", table_name="decision_apply_intent")
    op.drop_index("ix_decision_apply_intent_user", table_name="decision_apply_intent")
    op.drop_table("decision_apply_intent")
