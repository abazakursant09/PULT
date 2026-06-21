"""SEO Engine data foundation (A2) — seo_audit / seo_problem / seo_rule_evaluation / seo_signal

Additive, non-destructive, no backfill, no touch to existing tables. Four
append-only tables for the SEO Engine storage layer. Marketplace-agnostic
(marketplace is a plain string column, never an enum/check). SQLite-safe
(CREATE TABLE + CREATE INDEX only) and Postgres-compatible. No rule logic, no
engine — schema only.

Revision ID: se1a2b3c4d01
Revises: ma1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "se1a2b3c4d01"
down_revision: Union[str, None] = "ma1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── seo_audit (append-only run record) ───────────────────────────────────
    op.create_table(
        "seo_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="completed"),
        sa.Column("rule_catalog_version", sa.String(length=20), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("internal_health_index", sa.Float(), nullable=True),
        sa.Column("total_problems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_not_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_severity", sa.String(length=10), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_seo_audit_user_listing", "seo_audit", ["user_id", "listing_id"])
    op.create_index("ix_seo_audit_status", "seo_audit", ["status"])

    # ── seo_problem (append-only detection record) ───────────────────────────
    op.create_table(
        "seo_problem",
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
    op.create_index("ix_seo_problem_audit", "seo_problem", ["audit_id"])
    op.create_index("ix_seo_problem_user_listing_type", "seo_problem",
                    ["user_id", "listing_id", "problem_type"])

    # ── seo_rule_evaluation (coverage ledger — not_found vs not_evaluated) ────
    op.create_table(
        "seo_rule_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("problem_type", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("audit_id", "problem_type", name="uq_seo_rule_eval_audit_type"),
    )
    op.create_index("ix_seo_rule_eval_audit", "seo_rule_evaluation", ["audit_id"])
    op.create_index("ix_seo_rule_eval_result", "seo_rule_evaluation", ["result"])

    # ── seo_signal (seller-facing decision record) ───────────────────────────
    op.create_table(
        "seo_signal",
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
    op.create_index("ix_seo_signal_user_listing", "seo_signal", ["user_id", "listing_id"])
    op.create_index("ix_seo_signal_insight", "seo_signal", ["insight_key"])
    op.create_index("ix_seo_signal_audit", "seo_signal", ["audit_id"])
    op.create_index("ix_seo_signal_status", "seo_signal", ["status"])


def downgrade() -> None:
    op.drop_index("ix_seo_signal_status", table_name="seo_signal")
    op.drop_index("ix_seo_signal_audit", table_name="seo_signal")
    op.drop_index("ix_seo_signal_insight", table_name="seo_signal")
    op.drop_index("ix_seo_signal_user_listing", table_name="seo_signal")
    op.drop_table("seo_signal")
    op.drop_index("ix_seo_rule_eval_result", table_name="seo_rule_evaluation")
    op.drop_index("ix_seo_rule_eval_audit", table_name="seo_rule_evaluation")
    op.drop_table("seo_rule_evaluation")
    op.drop_index("ix_seo_problem_user_listing_type", table_name="seo_problem")
    op.drop_index("ix_seo_problem_audit", table_name="seo_problem")
    op.drop_table("seo_problem")
    op.drop_index("ix_seo_audit_status", table_name="seo_audit")
    op.drop_index("ix_seo_audit_user_listing", table_name="seo_audit")
    op.drop_table("seo_audit")
