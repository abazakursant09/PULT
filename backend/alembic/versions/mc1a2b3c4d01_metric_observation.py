"""Metric Catalog foundation — observations table (canonical normalized facts)

Additive: one new table `observations`. Append-only store of canonical metric
values (Metric Catalog read side). No marketplace-specific columns; `marketplace`
is provenance only. Breaks zero existing readers.

Revision ID: mc1a2b3c4d01
Revises: pg1a2b3c4d01
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mc1a2b3c4d01"
down_revision: Union[str, None] = "pg1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("entity_grain", sa.String(length=10), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("metric_name", sa.String(length=40), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("quality", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_observation_user_metric_entity", "observations",
                    ["user_id", "metric_name", "entity_id"])
    op.create_index("ix_observation_user_observed", "observations",
                    ["user_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_observation_user_observed", table_name="observations")
    op.drop_index("ix_observation_user_metric_entity", table_name="observations")
    op.drop_table("observations")
