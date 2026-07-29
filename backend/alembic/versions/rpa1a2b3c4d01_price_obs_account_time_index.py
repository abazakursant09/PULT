"""PULT-LAUNCH-2.5E-2B-1 — ix_price_obs_account_time for observation retention (schema only)

Additive, minimal: ONE index for the future per-account age scan of the change-only price observation
history — (marketplace_account_id, fetched_at), keyed by fetched_at (the evidence time), NOT created_at.
It mirrors the promotion table's existing ix_promo_obs_account_time.

No new column, no data change, no backfill, no touch to any existing index or row. Works on a NON-EMPTY
table (CREATE INDEX is online-safe and portable across SQLite + PostgreSQL). Nothing prunes any row:
observation_retention_enabled is False and no cleanup service / DELETE / scheduler exists yet.

Revision ID: rpa1a2b3c4d01
Revises: eco1a2b3c4d01
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "rpa1a2b3c4d01"
down_revision: Union[str, None] = "eco1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "marketplace_price_observations"
_IX = "ix_price_obs_account_time"


def upgrade() -> None:
    op.create_index(_IX, _T, ["marketplace_account_id", "fetched_at"])


def downgrade() -> None:
    op.drop_index(_IX, table_name=_T)
