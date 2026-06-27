"""Operations signal data foundation (Slice 1) — operations_signal

Additive, non-destructive, no backfill, no touch to existing tables. One new
canonical contour signal table mirroring the other contour signal tables
(seo/advertising/review/growth/legal/pricing): a lifecycle entity keyed by
insight_key `operations_<problem_type>:<marketplace>:<sku>`, carrying the five PULT
doctrine fields + evidence_hash for reconciliation. Marketplace-agnostic, soft refs.
SQLite-safe (CREATE TABLE + CREATE INDEX) and Postgres-compatible. No binding logic,
no executor, no payload — schema only.

Revision ID: ops1a2b3c4d01
Revises: tm1c1a2b3c4d02
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ops1a2b3c4d01"
down_revision: Union[str, None] = "tm1c1a2b3c4d02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operations_signal",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=True),
        sa.Column("problem_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("signal_key", sa.String(length=64), nullable=False),
        sa.Column("insight_key", sa.String(length=64), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("recommended_action_key", sa.String(length=64), nullable=True),
        sa.Column("alternative_action_keys", sa.Text(), nullable=True),
        sa.Column("what", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("meaning", sa.Text(), nullable=True),
        sa.Column("what_to_do", sa.Text(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column("priority_level", sa.String(length=10), nullable=True),
        sa.Column("effect_type", sa.String(length=40), nullable=True),
        sa.Column("effect_band", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_operations_signal_user_listing", "operations_signal", ["user_id", "listing_id"])
    op.create_index("ix_operations_signal_insight", "operations_signal", ["insight_key"])
    op.create_index("ix_operations_signal_status", "operations_signal", ["status"])


def downgrade() -> None:
    op.drop_index("ix_operations_signal_status", table_name="operations_signal")
    op.drop_index("ix_operations_signal_insight", table_name="operations_signal")
    op.drop_index("ix_operations_signal_user_listing", table_name="operations_signal")
    op.drop_table("operations_signal")
