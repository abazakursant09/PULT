"""Promotion Activation data foundation (A2) — promotion_run

Additive, non-destructive, no backfill, no touch to existing tables. One append-only
ledger recording each run of the promotion activator (candidates_seen / links_created
/ decisions_created / skipped). Marketplace-agnostic, soft refs. SQLite-safe (CREATE
TABLE + CREATE INDEX) and Postgres-compatible. No execution, no scheduler, no API —
schema only.

Revision ID: prom1a2b3c4d01
Revises: dauxp1a2b3c4d01
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "prom1a2b3c4d01"
down_revision: Union[str, None] = "dauxp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("contour", sa.String(length=20), nullable=True),
        sa.Column("candidates_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("links_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decisions_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("triggered_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_promotion_run_user", "promotion_run", ["user_id"])
    op.create_index("ix_promotion_run_contour", "promotion_run", ["contour"])


def downgrade() -> None:
    op.drop_index("ix_promotion_run_contour", table_name="promotion_run")
    op.drop_index("ix_promotion_run_user", table_name="promotion_run")
    op.drop_table("promotion_run")
