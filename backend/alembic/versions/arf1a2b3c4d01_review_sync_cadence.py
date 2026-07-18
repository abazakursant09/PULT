"""AR-AUTO-FILL — review-sync cadence (additive)

Throttles the scheduled auto-sync, all on marketplace_connections:
  * review_sync_next_at / review_sync_fail_count — per-connection cadence: 15-min interval after a
    success, exponential backoff (capped) after failures;
  * review_sync_cursor — a persistent keyset cursor over the connection's products (by Product.id),
    so each sync processes only the next batch and a large store is covered over several cycles.
Purely additive; no data moved; existing rows get NULLs / 0.

Revision ID: arf1a2b3c4d01
Revises: arc1a2b3c4d01
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "arf1a2b3c4d01"
down_revision: Union[str, None] = "arc1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("marketplace_connections",
                  sa.Column("review_sync_next_at", sa.DateTime(), nullable=True))
    op.add_column("marketplace_connections",
                  sa.Column("review_sync_fail_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("marketplace_connections",
                  sa.Column("review_sync_cursor", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("marketplace_connections", "review_sync_cursor")
    op.drop_column("marketplace_connections", "review_sync_fail_count")
    op.drop_column("marketplace_connections", "review_sync_next_at")
