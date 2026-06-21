"""Decision Outcome / Effect Loop data foundation (A2) — link + effect observation

Additive, non-destructive, no backfill, no touch to existing tables. Two tables
that will close the Signal → Decision → Execution → Measurement → Effect loop the
engines currently leave open. engine_signal_decision_link is the binding ledger
(unique per seller/insight/action); engine_effect_observation is the append-only
proof of what was actually observed (qualitative effect_band only — no score, no
forecast, no ROI, no money). Marketplace-agnostic, soft refs. SQLite-safe
(CREATE TABLE + CREATE INDEX) and Postgres-compatible. No bridge, no promotion, no
measurement — schema only.

NOTE: revision id is eo1a2b3c4d01 (Effect Outcome) — do1a2b3c4d01 was already taken
by the legacy decision_outcome migration.

Revision ID: eo1a2b3c4d01
Revises: lg2a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "eo1a2b3c4d01"
down_revision: Union[str, None] = "lg2a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── engine_signal_decision_link (binding ledger) ─────────────────────────
    op.create_table(
        "engine_signal_decision_link",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("contour", sa.String(length=20), nullable=False),
        sa.Column("signal_table", sa.String(length=40), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("insight_key", sa.String(length=80), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=36), nullable=True),
        sa.Column("link_status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "insight_key", "action_key",
                            name="uq_engine_link_user_insight_action"),
    )
    op.create_index("ix_engine_link_user_contour", "engine_signal_decision_link",
                    ["user_id", "contour"])
    op.create_index("ix_engine_link_insight", "engine_signal_decision_link", ["insight_key"])
    op.create_index("ix_engine_link_decision", "engine_signal_decision_link", ["decision_id"])
    op.create_index("ix_engine_link_status", "engine_signal_decision_link", ["link_status"])

    # ── engine_effect_observation (append-only proof) ────────────────────────
    op.create_table(
        "engine_effect_observation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("link_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("insight_key", sa.String(length=80), nullable=False),
        sa.Column("metric_key", sa.String(length=40), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("baseline_captured_at", sa.DateTime(), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=True),
        sa.Column("effect_band", sa.String(length=15), nullable=False, server_default="not_evaluated"),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_engine_effect_link", "engine_effect_observation", ["link_id"])
    op.create_index("ix_engine_effect_insight", "engine_effect_observation", ["insight_key"])
    op.create_index("ix_engine_effect_band", "engine_effect_observation", ["effect_band"])


def downgrade() -> None:
    op.drop_index("ix_engine_effect_band", table_name="engine_effect_observation")
    op.drop_index("ix_engine_effect_insight", table_name="engine_effect_observation")
    op.drop_index("ix_engine_effect_link", table_name="engine_effect_observation")
    op.drop_table("engine_effect_observation")
    op.drop_index("ix_engine_link_status", table_name="engine_signal_decision_link")
    op.drop_index("ix_engine_link_decision", table_name="engine_signal_decision_link")
    op.drop_index("ix_engine_link_insight", table_name="engine_signal_decision_link")
    op.drop_index("ix_engine_link_user_contour", table_name="engine_signal_decision_link")
    op.drop_table("engine_signal_decision_link")
