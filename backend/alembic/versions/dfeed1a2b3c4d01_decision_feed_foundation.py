"""Daily Decision Feed data foundation (A2) — decision_feed_state

Additive, non-destructive, no backfill, no touch to existing tables. One table for
the per-user attention state of a feed item — the Daily Decision Feed never stores
or duplicates a signal; this table holds only seen/snoozed/dismissed lifecycle,
keyed by a canonical item_key (canonical insight_key, or decision_id for Decision
Outcome). Marketplace-agnostic, soft refs. SQLite-safe (CREATE TABLE + CREATE
INDEX) and Postgres-compatible. No aggregation, no ranking, no API — schema only.

Revision ID: dfeed1a2b3c4d01
Revises: eo1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "dfeed1a2b3c4d01"
down_revision: Union[str, None] = "eo1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_feed_state",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("item_key", sa.String(length=80), nullable=False),
        sa.Column("contour", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=15), nullable=False, server_default="new"),
        sa.Column("snooze_until", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "item_key", name="uq_feed_state_user_item"),
    )
    op.create_index("ix_feed_state_user_state", "decision_feed_state", ["user_id", "state"])
    op.create_index("ix_feed_state_contour", "decision_feed_state", ["contour"])


def downgrade() -> None:
    op.drop_index("ix_feed_state_contour", table_name="decision_feed_state")
    op.drop_index("ix_feed_state_user_state", table_name="decision_feed_state")
    op.drop_table("decision_feed_state")
