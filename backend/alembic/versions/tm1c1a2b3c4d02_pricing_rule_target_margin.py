"""Pricing rule target margin (A4-margin-target) — pricing_rule.target_margin_pct

Additive, non-destructive, no backfill. One nullable column on the existing
pricing_rule table holding the seller's explicit net-margin target (percent, e.g.
25.0 = 25%). null = unset → the cost-plus set_price bind stays payload_not_derivable
(honest). SQLite-safe (add_column) and Postgres-compatible. No binding/executor/
payload change here — schema only.

Revision ID: tm1c1a2b3c4d02
Revises: pr1c1a2b3c4d01
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "tm1c1a2b3c4d02"
down_revision: Union[str, None] = "pr1c1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pricing_rules", sa.Column("target_margin_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("pricing_rules", "target_margin_pct")
