"""PULT-LAUNCH-2.5D-WB — club_buyer_price on MarketplacePriceObservation (feature OFF)

Additive and minimal: one nullable column so the WB read-only ingest can store the proven WB Club
(WB Кошелёк) buyer price (`clubDiscountedPrice`) as a SEPARATE buyer price, distinct from buyer_price
(`discountedPrice`) and catalog_price (`price`). It is a buyer price, never the seller's revenue; the
gap vs buyer_price is never treated as a subsidy.

  * club_buyer_price Numeric(18,2) NULL — 0 is a real value; a negative value is rejected by the named
    CHECK ck_price_obs_club_nonneg (created inline on SQLite, as a table constraint on PostgreSQL).

No backfill: existing rows get NULL. No other column, no new model, no vocabulary change. Every runtime
path is untouched; the feature flag stays OFF. downgrade drops ONLY the new column (and its CHECK).

Revision ID: wcb1a2b3c4d01
Revises: ozp1a2b3c4d01
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "wcb1a2b3c4d01"
down_revision: Union[str, None] = "ozp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "marketplace_price_observations"
_CK = "ck_price_obs_club_nonneg"
_CK_SQL = "club_buyer_price IS NULL OR club_buyer_price >= 0"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        # SQLite has no ALTER TABLE ADD CONSTRAINT; a column-level CHECK in ADD COLUMN is the portable
        # way to get the named constraint without rebuilding the table (which would risk dropping the
        # existing composite FKs / CHECK matrix).
        op.execute(
            f'ALTER TABLE {_T} ADD COLUMN club_buyer_price NUMERIC(18, 2) '
            f'CONSTRAINT {_CK} CHECK ({_CK_SQL})')
    else:
        op.add_column(_T, sa.Column("club_buyer_price", sa.Numeric(18, 2), nullable=True))
        op.create_check_constraint(_CK, _T, _CK_SQL)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.drop_column(_T, "club_buyer_price")   # native DROP COLUMN removes the inline CHECK too
    else:
        op.drop_constraint(_CK, _T, type_="check")
        op.drop_column(_T, "club_buyer_price")
