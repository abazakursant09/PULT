"""LEGAL-1B — remove behavioral personalization storage.

149-FZ art. 10.2-2 (рекомендательные технологии): PULT must not collect / systematize / analyse a
specific user's behavioural preferences to change the information it shows. This migration removes the
two on-disk behavioural stores that fed that layer:

  1. user_events        — the click / open / dismiss / snooze telemetry (preference signals);
  2. operator_decisions — the per-user accept / ignore / dismiss choice history that built the
                          operator-preference profile.

Both had only ever existed in local dev DBs and ephemeral CI databases — no staging / production
deployment exists (data gate proven for LEGAL-1B) — so no real behavioural data is destroyed. No backup /
audit / fingerprint copy is made (copying the preference history would defeat the legal purpose).

Neither table has an INBOUND foreign key (user_id is a bare String, no FK), so a plain DROP TABLE is safe
on both SQLite and PostgreSQL and touches no other table. users / products / stores / decisions and the
objective outcome-learning tables (decision_memory, engine_effect_observation, …) are untouched.

DOWNGRADE recreates BOTH tables EMPTY with their exact original columns / types / nullability / indexes
(from baseline 47beea1df0c1). It does NOT and CANNOT restore the deleted events, choices, counters or
preference history — those are gone by design.

Revision ID: rbp1a2b3c4d01
Revises: lch1a2b3c4d01
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "rbp1a2b3c4d01"
down_revision: Union[str, None] = "lch1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DROP TABLE removes each table and its own indexes on both dialects. No inbound FK exists.
    op.drop_table("user_events")
    op.drop_table("operator_decisions")


def downgrade() -> None:
    # Recreate the EMPTY structures only — exact baseline schema (47beea1df0c1). No data is restored.
    op.create_table(
        "operator_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("insight_type", sa.String(length=100), nullable=False),
        sa.Column("marketplace", sa.String(length=50), nullable=True),
        sa.Column("product_name", sa.String(length=200), nullable=True),
        sa.Column("action_taken", sa.String(length=30), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("ignored", sa.Boolean(), nullable=False),
        sa.Column("resolved_after_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("effect_observed", sa.String(length=30), nullable=True),
        sa.Column("effect_duration_days", sa.Integer(), nullable=True),
        sa.Column("recurrence_after_days", sa.Integer(), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_op_decision_user_created", "operator_decisions", ["user_id", "created_at"], unique=False)
    op.create_index("ix_op_decision_user_type", "operator_decisions", ["user_id", "insight_type"], unique=False)

    op.create_table(
        "user_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_scope", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_events_user_created", "user_events", ["user_id", "created_at"], unique=False)
    op.create_index("ix_user_events_user_id", "user_events", ["user_id"], unique=False)
    op.create_index("ix_user_events_user_type", "user_events", ["user_id", "event_type"], unique=False)
