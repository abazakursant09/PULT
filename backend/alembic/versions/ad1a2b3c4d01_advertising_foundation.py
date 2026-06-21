"""Advertising Engine data foundation (A2) — advertising_audit / problem / rule_evaluation / signal

Additive, non-destructive, no backfill, no touch to existing tables. Four
append-only tables for the Advertising Engine storage layer (detection layer
immutable; signal is the lifecycle entity). Marketplace-agnostic. SQLite-safe
(CREATE TABLE + CREATE INDEX) and Postgres-compatible. No rule logic, no API, no
write, no ad-cabinet — schema only.

Revision ID: ad1a2b3c4d01
Revises: se1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ad1a2b3c4d01"
down_revision: Union[str, None] = "se1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── advertising_audit (append-only run record) ───────────────────────────
    op.create_table(
        "advertising_audit",
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
    op.create_index("ix_adv_audit_user_listing", "advertising_audit", ["user_id", "listing_id"])
    op.create_index("ix_adv_audit_status", "advertising_audit", ["status"])

    # ── advertising_problem (append-only detection record) ───────────────────
    op.create_table(
        "advertising_problem",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("estimated_effect_type", sa.String(length=40), nullable=True),
        sa.Column("detectability", sa.String(length=20), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_adv_problem_audit", "advertising_problem", ["audit_id"])
    op.create_index("ix_adv_problem_user_listing_type", "advertising_problem",
                    ["user_id", "listing_id", "problem_type"])

    # ── advertising_rule_evaluation (coverage ledger) ────────────────────────
    op.create_table(
        "advertising_rule_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("audit_id", "problem_type", name="uq_adv_rule_eval_audit_type"),
    )
    op.create_index("ix_adv_rule_eval_audit", "advertising_rule_evaluation", ["audit_id"])
    op.create_index("ix_adv_rule_eval_result", "advertising_rule_evaluation", ["result"])

    # ── advertising_signal (seller-facing decision record, lifecycle) ────────
    op.create_table(
        "advertising_signal",
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
        sa.Column("recommended_action_key", sa.String(length=64), nullable=True),
        sa.Column("alternative_action_keys", sa.Text(), nullable=True),
        sa.Column("what", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("meaning", sa.Text(), nullable=True),
        sa.Column("what_to_do", sa.Text(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column("priority_level", sa.String(length=10), nullable=True),
        sa.Column("expected_effect_type", sa.String(length=40), nullable=True),
        sa.Column("effect_band", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_adv_signal_user_listing", "advertising_signal", ["user_id", "listing_id"])
    op.create_index("ix_adv_signal_insight", "advertising_signal", ["insight_key"])
    op.create_index("ix_adv_signal_audit", "advertising_signal", ["audit_id"])
    op.create_index("ix_adv_signal_status", "advertising_signal", ["status"])


def downgrade() -> None:
    op.drop_index("ix_adv_signal_status", table_name="advertising_signal")
    op.drop_index("ix_adv_signal_audit", table_name="advertising_signal")
    op.drop_index("ix_adv_signal_insight", table_name="advertising_signal")
    op.drop_index("ix_adv_signal_user_listing", table_name="advertising_signal")
    op.drop_table("advertising_signal")
    op.drop_index("ix_adv_rule_eval_result", table_name="advertising_rule_evaluation")
    op.drop_index("ix_adv_rule_eval_audit", table_name="advertising_rule_evaluation")
    op.drop_table("advertising_rule_evaluation")
    op.drop_index("ix_adv_problem_user_listing_type", table_name="advertising_problem")
    op.drop_index("ix_adv_problem_audit", table_name="advertising_problem")
    op.drop_table("advertising_problem")
    op.drop_index("ix_adv_audit_status", table_name="advertising_audit")
    op.drop_index("ix_adv_audit_user_listing", table_name="advertising_audit")
    op.drop_table("advertising_audit")
