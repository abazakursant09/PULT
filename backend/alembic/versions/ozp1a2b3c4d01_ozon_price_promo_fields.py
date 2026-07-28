"""PULT-LAUNCH-2.5D — two proven Ozon price/promo fields on MarketplacePriceObservation (feature OFF)

Additive and minimal: adds exactly two nullable columns to marketplace_price_observations so the
2.5D Ozon ingest can store raw, PROVEN values from /v5/product/info/prices without inventing meaning.

  * provider_min_price  Numeric(18,2) NULL — the provider's declared `min_price`, verbatim. It is
    NOT revenue, NOT a promo price, NOT a margin, NOT an emergency floor. 0 is a real value.
  * auto_action_enabled Boolean       NULL — the provider's auto-promotion flag. NULL means Ozon
    gave no proven value and is never coerced to False.

No new CHECK, no vocabulary change (participation_status is NOT extended — a candidate is eligibility,
never participation). No backfill: existing rows get NULL/NULL. Every runtime path is untouched;
the feature flag stays OFF. downgrade drops ONLY the two new columns.

Revision ID: ozp1a2b3c4d01
Revises: mpo1a2b3c4d01
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ozp1a2b3c4d01"
down_revision: Union[str, None] = "mpo1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "marketplace_price_observations"


def upgrade() -> None:
    # Nullable adds — SQLite (3.35+) and PostgreSQL both take these without a table rebuild, so the
    # existing composite FKs / CHECK matrix are left exactly as-is.
    op.add_column(_T, sa.Column("provider_min_price", sa.Numeric(18, 2), nullable=True))
    op.add_column(_T, sa.Column("auto_action_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column(_T, "auto_action_enabled")
    op.drop_column(_T, "provider_min_price")
