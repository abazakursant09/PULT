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


def _sqlite_enforce_last_verified_notnull(table: str) -> None:
    """Rebuild `table` on SQLite so last_verified_at becomes NOT NULL with NO server default, preserving
    EVERY existing column / FK / UNIQUE / CHECK / index / server default AND all rows.

    The rebuild copies the LITERAL stored DDL (only injecting NOT NULL on the one column) rather than a
    reflected schema: reflection re-emits a peer migration's COLUMN-INLINE CHECK (wcb's
    ck_price_obs_club_nonneg on club_buyer_price) as a TABLE-LEVEL constraint, which then breaks that
    migration's own SQLite `DROP COLUMN` downgrade. legacy_alter_table=ON keeps the RENAME from
    rewriting child foreign-key references to the temp name."""
    bind = op.get_bind()
    create_sql = bind.exec_driver_sql(
        f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'").scalar()
    index_sqls = [r[0] for r in bind.exec_driver_sql(
        f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{table}' "
        f"AND sql IS NOT NULL").fetchall()]
    cols = [r[1] for r in bind.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()]

    new_sql = create_sql.replace("last_verified_at DATETIME", "last_verified_at DATETIME NOT NULL", 1)
    if "last_verified_at DATETIME NOT NULL" not in new_sql:
        raise RuntimeError(f"eco: could not locate last_verified_at column in {table} DDL")
    tmp = f"{table}__eco_rebuild"
    collist = ", ".join(f'"{c}"' for c in cols)

    op.execute("PRAGMA legacy_alter_table=ON")
    op.execute(f'ALTER TABLE "{table}" RENAME TO "{tmp}"')
    op.execute(new_sql)                                                    # new {table}: club stays inline
    op.execute(f'INSERT INTO "{table}" ({collist}) SELECT {collist} FROM "{tmp}"')
    op.execute(f'DROP TABLE "{tmp}"')
    op.execute("PRAGMA legacy_alter_table=OFF")
    for isql in index_sqls:                                                # recreate the pre-existing indexes
        op.execute(isql)


def _add_change_only(table: str) -> None:
    # last_verified_at ends NOT NULL with NO server default (both dialects, matching the model);
    # evidence_fingerprint stays nullable. The migration supports EXISTING rows: last_verified_at is
    # added nullable, backfilled = fetched_at, THEN made NOT NULL (SQLite via a literal-DDL rebuild).
    dialect = op.get_bind().dialect.name
    op.add_column(table, sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True))
    op.add_column(table, sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(f"UPDATE {table} SET last_verified_at = fetched_at WHERE last_verified_at IS NULL")
    if dialect == "sqlite":
        _sqlite_enforce_last_verified_notnull(table)
    else:
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
