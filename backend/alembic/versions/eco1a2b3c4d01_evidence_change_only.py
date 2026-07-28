"""PULT-LAUNCH-2.5E-1 — change-only observation bookkeeping (feature OFF)

Additive to the two APPEND-ONLY parent observation tables so the ingest writers can stop appending
identical rows every run:

  * last_verified_at  DateTime(timezone=True) NOT NULL — the latest run that re-observed this exact
    evidence. Added nullable, backfilled = fetched_at, then made NOT NULL (both tables are empty on
    every real/CI database — the feature has never run — so the backfill is a vacuous no-op there).
  * evidence_fingerprint  String(64) NULLABLE — SHA-256 of the semantic fields. Left nullable and NOT
    backfilled: a fingerprint is never GUESSED from stored columns, and a NULL fingerprint makes the
    change-only writer insert a fresh version (fail-safe, never a wrong dedupe).

Plus one latest-of-series index per table, keyed by the SERIES identity (external_product_id, NOT the
resolved internal product_id) with fetched_at last. last_verified_at / evidence_fingerprint are
deliberately NOT indexed. No runtime path changes; the feature flag stays OFF.

Revision ID: eco1a2b3c4d01
Revises: ypo1a2b3c4d01
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "eco1a2b3c4d01"
down_revision: Union[str, None] = "ypo1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRICE = "marketplace_price_observations"
_PROMO = "marketplace_promotion_observations"


def _add_change_only(table: str) -> None:
    # last_verified_at is NOT NULL; evidence_fingerprint stays nullable. Both parent tables are EMPTY on
    # every real/CI database (the feature has never run), so "backfill = fetched_at" is a vacuous no-op.
    #
    # The NOT NULL is added WITHOUT rebuilding the table. A batch/recreate would reflect the peer
    # migration's COLUMN-INLINE checks (e.g. wcb's ck_price_obs_club_nonneg on club_buyer_price) back as
    # TABLE-LEVEL constraints, which then breaks that migration's own SQLite `DROP COLUMN` downgrade.
    dialect = op.get_bind().dialect.name
    op.add_column(table, sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True))
    if dialect == "sqlite":
        # SQLite cannot ALTER a column to NOT NULL, and a NOT NULL ADD COLUMN needs a non-NULL DEFAULT.
        # Add it NOT NULL with an inline sentinel DEFAULT (native ADD COLUMN, no table rebuild), then
        # backfill = fetched_at. The sentinel never applies (the tables are empty and the writer always
        # sets last_verified_at); it exists only to satisfy SQLite's ADD COLUMN rule.
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN last_verified_at DATETIME "
            f"NOT NULL DEFAULT '1970-01-01 00:00:00'")
        op.execute(f"UPDATE {table} SET last_verified_at = fetched_at")
    else:
        op.add_column(table, sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(f"UPDATE {table} SET last_verified_at = fetched_at WHERE last_verified_at IS NULL")
        op.alter_column(table, "last_verified_at",
                        existing_type=sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    _add_change_only(_PRICE)
    op.create_index("ix_price_obs_series", _PRICE,
                    ["marketplace_store_id", "external_product_id", "observation_kind",
                     "promotion_key", "source", "fetched_at"])

    _add_change_only(_PROMO)
    op.create_index("ix_promo_obs_series", _PROMO,
                    ["marketplace_account_id", "external_product_id", "promotion_id",
                     "source", "fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_promo_obs_series", table_name=_PROMO)
    op.drop_column(_PROMO, "evidence_fingerprint")
    op.drop_column(_PROMO, "last_verified_at")

    op.drop_index("ix_price_obs_series", table_name=_PRICE)
    op.drop_column(_PRICE, "evidence_fingerprint")
    op.drop_column(_PRICE, "last_verified_at")
