"""Legal Navigator A6 — add legal_signal.lifecycle_reason

Additive, non-destructive: one nullable column to record a cautious lifecycle note
on a legal_signal (e.g. "risk_not_detected_in_latest_audit") — NEVER "compliant".
No data backfill, no touch to other tables. SQLite-safe and Postgres-compatible.

Revision ID: lg2a2b3c4d01
Revises: lg1a2b3c4d01
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "lg2a2b3c4d01"
down_revision: Union[str, None] = "lg1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("legal_signal", sa.Column("lifecycle_reason", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("legal_signal", "lifecycle_reason")
