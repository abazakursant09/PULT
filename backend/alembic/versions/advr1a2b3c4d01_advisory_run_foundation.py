"""Advisory Runtime data foundation (Phase-0) — advisory_run

Additive, non-destructive, no backfill, no touch to existing tables. One new
append-only ledger table for the Advisory Runtime: one row per (run, user,
producer) with timing/status + an OPAQUE producer-supplied `stats` blob. NO typed
counters (the Runtime does not know producer semantics). SQLite-safe (CREATE TABLE
+ CREATE INDEX) and Postgres-compatible. No producer, no scheduler, no execution.

Revision ID: advr1a2b3c4d01
Revises: ops1a2b3c4d01
Create Date: 2026-06-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "advr1a2b3c4d01"
down_revision: Union[str, None] = "ops1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "advisory_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("producer_key", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=True),
        sa.Column("stats", sa.Text(), nullable=True),
    )
    op.create_index("ix_advisory_run_user_producer_started", "advisory_run",
                    ["user_id", "producer_key", "started_at"])
    op.create_index("ix_advisory_run_run_id", "advisory_run", ["run_id"])
    op.create_index("ix_advisory_run_status", "advisory_run", ["status"])


def downgrade() -> None:
    op.drop_index("ix_advisory_run_status", table_name="advisory_run")
    op.drop_index("ix_advisory_run_run_id", table_name="advisory_run")
    op.drop_index("ix_advisory_run_user_producer_started", table_name="advisory_run")
    op.drop_table("advisory_run")
