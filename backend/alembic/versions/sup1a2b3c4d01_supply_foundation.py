"""Supply / Replenishment Diagnosis contour data foundation (Phase 4.0) — supply_signal + supply_audit

Additive, non-destructive, no backfill, no touch to existing tables. Two new tables
mirroring the Growth contour (growth_signal / growth_audit) field-for-field: a lifecycle
signal keyed by insight_key `supply_<problem_type>:<marketplace>:<sku>` carrying the five
PULT doctrine fields + evidence_hash for reconciliation, and an append-only audit run.
Marketplace-agnostic, soft refs. SQLite-safe (CREATE TABLE + CREATE INDEX) and
Postgres-compatible. Diagnosis-only: no binding, no executor, no payload — schema only,
wired to nothing. Does NOT reuse operations_signal.

Revision ID: sup1a2b3c4d01
Revises: mlk1a2b3c4d01
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "sup1a2b3c4d01"
down_revision: Union[str, None] = "mlk1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supply_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="completed"),
        sa.Column("rule_catalog_version", sa.String(length=20), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("total_problems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_not_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_severity", sa.String(length=10), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_supply_audit_user_listing", "supply_audit", ["user_id", "listing_id"])
    op.create_index("ix_supply_audit_status", "supply_audit", ["status"])

    op.create_table(
        "supply_signal",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
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
    op.create_index("ix_supply_signal_user_listing", "supply_signal", ["user_id", "listing_id"])
    op.create_index("ix_supply_signal_insight", "supply_signal", ["insight_key"])
    op.create_index("ix_supply_signal_audit", "supply_signal", ["audit_id"])
    op.create_index("ix_supply_signal_status", "supply_signal", ["status"])
    op.create_index("ix_supply_signal_category", "supply_signal", ["category"])


def downgrade() -> None:
    op.drop_index("ix_supply_signal_category", table_name="supply_signal")
    op.drop_index("ix_supply_signal_status", table_name="supply_signal")
    op.drop_index("ix_supply_signal_audit", table_name="supply_signal")
    op.drop_index("ix_supply_signal_insight", table_name="supply_signal")
    op.drop_index("ix_supply_signal_user_listing", table_name="supply_signal")
    op.drop_table("supply_signal")

    op.drop_index("ix_supply_audit_status", table_name="supply_audit")
    op.drop_index("ix_supply_audit_user_listing", table_name="supply_audit")
    op.drop_table("supply_audit")
