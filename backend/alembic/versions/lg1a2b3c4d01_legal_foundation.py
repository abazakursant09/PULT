"""Legal Navigator data foundation (A2) — legal_audit / finding / rule_evaluation / signal

Additive, non-destructive, no backfill, no touch to existing tables. Four
append-only tables for the Legal Navigator storage layer (detection layer
immutable; signal is the lifecycle entity). Legal Navigator is a recommendation
contour — never a legal conclusion, never a guarantee. Marketplace-agnostic.
SQLite-safe (CREATE TABLE + CREATE INDEX) and Postgres-compatible. No rule logic,
no API, no scoring — schema only.

Revision ID: lg1a2b3c4d01
Revises: gr1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "lg1a2b3c4d01"
down_revision: Union[str, None] = "gr1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── legal_audit (append-only run record) ─────────────────────────────────
    op.create_table(
        "legal_audit",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("subject_type", sa.String(length=20), nullable=True),
        sa.Column("subject_ref", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="completed"),
        sa.Column("rule_catalog_version", sa.String(length=20), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("total_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_not_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_severity", sa.String(length=10), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_legal_audit_user_listing", "legal_audit", ["user_id", "listing_id"])
    op.create_index("ix_legal_audit_status", "legal_audit", ["status"])

    # ── legal_finding (append-only detection record) ─────────────────────────
    op.create_table(
        "legal_finding",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("subject_type", sa.String(length=20), nullable=True),
        sa.Column("subject_ref", sa.String(length=255), nullable=True),
        sa.Column("requirement_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("risk_level", sa.String(length=10), nullable=True),
        sa.Column("estimated_effect_type", sa.String(length=40), nullable=True),
        sa.Column("detectability", sa.String(length=20), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_legal_finding_audit", "legal_finding", ["audit_id"])
    op.create_index("ix_legal_finding_user_listing_type", "legal_finding",
                    ["user_id", "listing_id", "requirement_type"])
    op.create_index("ix_legal_finding_category", "legal_finding", ["category"])

    # ── legal_rule_evaluation (coverage ledger) ──────────────────────────────
    op.create_table(
        "legal_rule_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("requirement_type", sa.String(length=40), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("audit_id", "requirement_type", name="uq_legal_rule_eval_audit_type"),
    )
    op.create_index("ix_legal_rule_eval_audit", "legal_rule_evaluation", ["audit_id"])
    op.create_index("ix_legal_rule_eval_result", "legal_rule_evaluation", ["result"])

    # ── legal_signal (seller-facing recommendation record, lifecycle) ────────
    op.create_table(
        "legal_signal",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("finding_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=True),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("subject_type", sa.String(length=20), nullable=True),
        sa.Column("subject_ref", sa.String(length=255), nullable=True),
        sa.Column("signal_key", sa.String(length=64), nullable=False),
        sa.Column("insight_key", sa.String(length=64), nullable=True),
        sa.Column("requirement_type", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("recommended_action_key", sa.String(length=64), nullable=True),
        sa.Column("alternative_action_keys", sa.Text(), nullable=True),
        sa.Column("what", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("meaning", sa.Text(), nullable=True),
        sa.Column("what_to_do", sa.Text(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column("priority_level", sa.String(length=10), nullable=True),
        sa.Column("risk_level", sa.String(length=10), nullable=True),
        sa.Column("effect_type", sa.String(length=40), nullable=True),
        sa.Column("effect_band", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_legal_signal_user_listing", "legal_signal", ["user_id", "listing_id"])
    op.create_index("ix_legal_signal_insight", "legal_signal", ["insight_key"])
    op.create_index("ix_legal_signal_audit", "legal_signal", ["audit_id"])
    op.create_index("ix_legal_signal_status", "legal_signal", ["status"])
    op.create_index("ix_legal_signal_category", "legal_signal", ["category"])


def downgrade() -> None:
    for ix in ("ix_legal_signal_category", "ix_legal_signal_status", "ix_legal_signal_audit",
               "ix_legal_signal_insight", "ix_legal_signal_user_listing"):
        op.drop_index(ix, table_name="legal_signal")
    op.drop_table("legal_signal")
    op.drop_index("ix_legal_rule_eval_result", table_name="legal_rule_evaluation")
    op.drop_index("ix_legal_rule_eval_audit", table_name="legal_rule_evaluation")
    op.drop_table("legal_rule_evaluation")
    op.drop_index("ix_legal_finding_category", table_name="legal_finding")
    op.drop_index("ix_legal_finding_user_listing_type", table_name="legal_finding")
    op.drop_index("ix_legal_finding_audit", table_name="legal_finding")
    op.drop_table("legal_finding")
    op.drop_index("ix_legal_audit_status", table_name="legal_audit")
    op.drop_index("ix_legal_audit_user_listing", table_name="legal_audit")
    op.drop_table("legal_audit")
