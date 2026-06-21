"""Review Assistant data foundation (A2) — review_audit / problem / rule_evaluation / signal

Additive, non-destructive, no backfill, no touch to existing tables. Four
append-only tables for the Review Assistant storage layer (detection layer
immutable; signal is the lifecycle entity, carrying safety_category/safety_mode
for the Human-Control + Negative-Review doctrine). Marketplace-agnostic.
SQLite-safe (CREATE TABLE + CREATE INDEX) and Postgres-compatible. No rule logic,
no API, no autoresponder — schema only.

Revision ID: rv1a2b3c4d01
Revises: ad1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rv1a2b3c4d01"
down_revision: Union[str, None] = "ad1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── review_audit (append-only run record) ────────────────────────────────
    op.create_table(
        "review_audit",
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
    op.create_index("ix_review_audit_user_listing", "review_audit", ["user_id", "listing_id"])
    op.create_index("ix_review_audit_status", "review_audit", ["status"])

    # ── review_problem (append-only detection record) ────────────────────────
    op.create_table(
        "review_problem",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("review_id", sa.String(length=36), nullable=True),
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
    op.create_index("ix_review_problem_audit", "review_problem", ["audit_id"])
    op.create_index("ix_review_problem_user_listing_type", "review_problem",
                    ["user_id", "listing_id", "problem_type"])
    op.create_index("ix_review_problem_review", "review_problem", ["review_id"])

    # ── review_rule_evaluation (coverage ledger) ─────────────────────────────
    op.create_table(
        "review_rule_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("audit_id", "problem_type", name="uq_review_rule_eval_audit_type"),
    )
    op.create_index("ix_review_rule_eval_audit", "review_rule_evaluation", ["audit_id"])
    op.create_index("ix_review_rule_eval_result", "review_rule_evaluation", ["result"])

    # ── review_signal (seller-facing decision record, lifecycle) ─────────────
    op.create_table(
        "review_signal",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("problem_id", sa.String(length=36), nullable=True),
        sa.Column("review_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("signal_key", sa.String(length=64), nullable=False),
        sa.Column("insight_key", sa.String(length=64), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("safety_category", sa.String(length=20), nullable=True),
        sa.Column("safety_mode", sa.String(length=20), nullable=True),
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
    op.create_index("ix_review_signal_user_listing", "review_signal", ["user_id", "listing_id"])
    op.create_index("ix_review_signal_insight", "review_signal", ["insight_key"])
    op.create_index("ix_review_signal_audit", "review_signal", ["audit_id"])
    op.create_index("ix_review_signal_status", "review_signal", ["status"])
    op.create_index("ix_review_signal_review", "review_signal", ["review_id"])


def downgrade() -> None:
    for ix in ("ix_review_signal_review", "ix_review_signal_status", "ix_review_signal_audit",
               "ix_review_signal_insight", "ix_review_signal_user_listing"):
        op.drop_index(ix, table_name="review_signal")
    op.drop_table("review_signal")
    op.drop_index("ix_review_rule_eval_result", table_name="review_rule_evaluation")
    op.drop_index("ix_review_rule_eval_audit", table_name="review_rule_evaluation")
    op.drop_table("review_rule_evaluation")
    op.drop_index("ix_review_problem_review", table_name="review_problem")
    op.drop_index("ix_review_problem_user_listing_type", table_name="review_problem")
    op.drop_index("ix_review_problem_audit", table_name="review_problem")
    op.drop_table("review_problem")
    op.drop_index("ix_review_audit_status", table_name="review_audit")
    op.drop_index("ix_review_audit_user_listing", table_name="review_audit")
    op.drop_table("review_audit")
