"""PULT-LAUNCH-2.5D-Yandex-B3 — account-level Yandex promotion evidence (feature OFF)

Additive: two APPEND-ONLY tables for business-level Yandex promo participation evidence, at the level
the API actually proves it (NOT store-scoped like MarketplacePriceObservation).

  * marketplace_promotion_observations (PARENT) — one row per (account, offer, promo, run): verbatim
    provider_status + normalized participation/auto/attribution + promo prices. Composite FKs pin the
    cabinet (account+marketplace) and a resolved product (product+account), both CASCADE. Fixed
    provenance (api / promos / yandex_promo). No revenue/subsidy/commission column exists.
  * marketplace_promotion_store_evidence (CHILD) — the proven campaignIds of a PARTIALLY_AUTO parent,
    each mapped to a real store of the same cabinet or kept 'unmapped'. Composite FK to the store
    (store+account) CASCADE; parent FK CASCADE.

No backfill (no rows exist). downgrade drops child then parent. Every runtime path untouched; the
feature flag stays OFF (the ingest writer is added in the same PR but reached only through
run_api_sync_once, which makes zero calls while disabled).

Revision ID: ypo1a2b3c4d01
Revises: wcb1a2b3c4d01
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ypo1a2b3c4d01"
down_revision: Union[str, None] = "wcb1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PARENT = "marketplace_promotion_observations"
_CHILD = "marketplace_promotion_store_evidence"
_MONEY = ("pre_promo_price", "promo_buyer_price", "promo_max_price")


def upgrade() -> None:
    op.create_table(
        _PARENT,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ingest_run_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_account_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("external_product_id", sa.String(length=255), nullable=False),
        sa.Column("resolution_status", sa.String(length=12), nullable=False),
        sa.Column("promotion_id", sa.String(length=128), nullable=False),
        sa.Column("promotion_type", sa.String(length=16), nullable=False),
        sa.Column("provider_status", sa.String(length=64), nullable=False),
        sa.Column("participation_status", sa.String(length=20), nullable=False),
        sa.Column("auto_participation", sa.Boolean(), nullable=True),
        sa.Column("attribution_status", sa.String(length=16), nullable=False),
        sa.Column("pre_promo_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("promo_buyer_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("promo_max_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("currency_status", sa.String(length=8), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("provider_dataset", sa.String(length=20), nullable=False),
        sa.Column("promotion_start_at", sa.DateTime(), nullable=True),
        sa.Column("promotion_end_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["marketplace_account_id", "marketplace"],
            ["marketplace_accounts.id", "marketplace_accounts.marketplace"],
            ondelete="CASCADE", name="fk_promo_obs_account"),
        sa.ForeignKeyConstraint(
            ["product_id", "marketplace_account_id"],
            ["products.id", "products.marketplace_account_id"],
            ondelete="CASCADE", name="fk_promo_obs_product"),
        sa.UniqueConstraint("marketplace_account_id", "external_product_id", "promotion_id",
                            "source", "ingest_run_id", name="uq_promo_obs_run"),
        sa.CheckConstraint("marketplace = 'yandex'", name="ck_promo_obs_marketplace"),
        sa.CheckConstraint("source = 'api'", name="ck_promo_obs_source"),
        sa.CheckConstraint("provider_dataset = 'promos'", name="ck_promo_obs_dataset"),
        sa.CheckConstraint("promotion_type = 'yandex_promo'", name="ck_promo_obs_promotion_type"),
        sa.CheckConstraint(
            "provider_status <> '' AND provider_status = trim(provider_status) "
            "AND length(provider_status) <= 64", name="ck_promo_obs_provider_status_clean"),
        sa.CheckConstraint("resolution_status IN ('resolved', 'unassigned')",
                           name="ck_promo_obs_resolution_status"),
        sa.CheckConstraint("participation_status IN ('active', 'not_participating', 'unknown')",
                           name="ck_promo_obs_participation"),
        sa.CheckConstraint(
            "attribution_status IN ('account_wide', 'exact_stores', 'unresolved', 'unmapped_stores')",
            name="ck_promo_obs_attribution"),
        sa.CheckConstraint("currency_status IN ('proven', 'unknown')", name="ck_promo_obs_currency_status"),
        sa.CheckConstraint(
            "(resolution_status = 'resolved' AND product_id IS NOT NULL) OR "
            "(resolution_status = 'unassigned' AND product_id IS NULL)",
            name="ck_promo_obs_resolution"),
        sa.CheckConstraint("attribution_status <> 'exact_stores' OR provider_status = 'PARTIALLY_AUTO'",
                           name="ck_promo_obs_exact_stores"),
        sa.CheckConstraint(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in _MONEY),
                           name="ck_promo_obs_money_nonneg"),
        sa.CheckConstraint("currency IS NULL OR (currency = upper(currency) AND length(currency) = 3)",
                           name="ck_promo_obs_currency_fmt"),
        sa.CheckConstraint("currency_status <> 'proven' OR currency IS NOT NULL",
                           name="ck_promo_obs_currency_proven"),
        sa.CheckConstraint(
            "promotion_end_at IS NULL OR promotion_start_at IS NULL "
            "OR promotion_end_at >= promotion_start_at", name="ck_promo_obs_period"),
    )
    op.create_index("ix_promo_obs_account_time", _PARENT, ["marketplace_account_id", "fetched_at"])
    op.create_index("ix_promo_obs_latest", _PARENT,
                    ["marketplace_account_id", "product_id", "promotion_id", "fetched_at"])
    op.create_index("ix_promo_obs_promotion", _PARENT, ["promotion_id"])

    op.create_table(
        _CHILD,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("promotion_observation_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_account_id", sa.String(length=36), nullable=False),
        sa.Column("external_store_id", sa.String(length=128), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36), nullable=True),
        sa.Column("mapping_status", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["promotion_observation_id"],
                                [f"{_PARENT}.id"], ondelete="CASCADE", name="fk_promo_store_ev_parent"),
        sa.ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_promo_store_ev_store"),
        sa.UniqueConstraint("promotion_observation_id", "external_store_id", name="uq_promo_store_ev"),
        sa.CheckConstraint("mapping_status IN ('mapped', 'unmapped')", name="ck_promo_store_ev_mapping_vocab"),
        sa.CheckConstraint(
            "(mapping_status = 'mapped' AND marketplace_store_id IS NOT NULL) OR "
            "(mapping_status = 'unmapped' AND marketplace_store_id IS NULL)",
            name="ck_promo_store_ev_mapping"),
        sa.CheckConstraint(
            "external_store_id <> '' AND external_store_id = trim(external_store_id) "
            "AND external_store_id NOT LIKE '% %'", name="ck_promo_store_ev_external_clean"),
    )
    op.create_index("ix_promo_store_ev_account_ext", _CHILD,
                    ["marketplace_account_id", "external_store_id"])


def downgrade() -> None:
    op.drop_index("ix_promo_store_ev_account_ext", table_name=_CHILD)
    op.drop_table(_CHILD)
    op.drop_index("ix_promo_obs_promotion", table_name=_PARENT)
    op.drop_index("ix_promo_obs_latest", table_name=_PARENT)
    op.drop_index("ix_promo_obs_account_time", table_name=_PARENT)
    op.drop_table(_PARENT)
