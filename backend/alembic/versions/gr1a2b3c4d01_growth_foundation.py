"""Growth/Opportunity Engine data foundation (A2) — growth_audit / problem / rule_evaluation / signal

Additive, non-destructive, no backfill, no touch to existing tables. Four
append-only tables for the Growth Engine storage layer (detection layer immutable;
signal is the lifecycle entity). Growth finds unrealised opportunities, not
defects. Marketplace-agnostic. SQLite-safe (CREATE TABLE + CREATE INDEX) and
Postgres-compatible. No rule logic, no API, no scoring — schema only.

Revision ID: gr1a2b3c4d01
Revises: rv1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "gr1a2b3c4d01"
down_revision: Union[str, None] = "rv1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── growth_audit (append-only run record) ────────────────────────────────
    op.create_table(
        "growth_audit",
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
    op.create_index("ix_growth_audit_user_listing", "growth_audit", ["user_id", "listing_id"])
    op.create_index("ix_growth_audit_status", "growth_audit", ["status"])

    # ── growth_problem (append-only detection record) ────────────────────────
    op.create_table(
        "growth_problem",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("estimated_effect_type", sa.String(length=40), nullable=True),
        sa.Column("detectability", sa.String(length=20), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_growth_problem_audit", "growth_problem", ["audit_id"])
    op.create_index("ix_growth_problem_user_listing_type", "growth_problem",
                    ["user_id", "listing_id", "problem_type"])
    op.create_index("ix_growth_problem_category", "growth_problem", ["category"])

    # ── growth_rule_evaluation (coverage ledger) ─────────────────────────────
    op.create_table(
        "growth_rule_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("audit_id", "problem_type", name="uq_growth_rule_eval_audit_type"),
    )
    op.create_index("ix_growth_rule_eval_audit", "growth_rule_evaluation", ["audit_id"])
    op.create_index("ix_growth_rule_eval_result", "growth_rule_evaluation", ["result"])

    # ── growth_signal (seller-facing decision record, lifecycle) ─────────────
    op.create_table(
        "growth_signal",
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
    op.create_index("ix_growth_signal_user_listing", "growth_signal", ["user_id", "listing_id"])
    op.create_index("ix_growth_signal_insight", "growth_signal", ["insight_key"])
    op.create_index("ix_growth_signal_audit", "growth_signal", ["audit_id"])
    op.create_index("ix_growth_signal_status", "growth_signal", ["status"])
    op.create_index("ix_growth_signal_category", "growth_signal", ["category"])


def downgrade() -> None:
    for ix in ("ix_growth_signal_category", "ix_growth_signal_status", "ix_growth_signal_audit",
               "ix_growth_signal_insight", "ix_growth_signal_user_listing"):
        op.drop_index(ix, table_name="growth_signal")
    op.drop_table("growth_signal")
    op.drop_index("ix_growth_rule_eval_result", table_name="growth_rule_evaluation")
    op.drop_index("ix_growth_rule_eval_audit", table_name="growth_rule_evaluation")
    op.drop_table("growth_rule_evaluation")
    op.drop_index("ix_growth_problem_category", table_name="growth_problem")
    op.drop_index("ix_growth_problem_user_listing_type", table_name="growth_problem")
    op.drop_index("ix_growth_problem_audit", table_name="growth_problem")
    op.drop_table("growth_problem")
    op.drop_index("ix_growth_audit_status", table_name="growth_audit")
    op.drop_index("ix_growth_audit_user_listing", table_name="growth_audit")
    op.drop_table("growth_audit")
