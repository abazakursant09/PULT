"""PULT-LAUNCH-2.5C — MarketplacePriceObservation (proven price/currency storage, feature OFF)

Additive: creates the append-only marketplace_price_observations table with tenant composite FKs
(marketplace_stores + product_placements, both CASCADE), a run-uniqueness constraint, the full
CHECK matrix (enum dictionaries, resolution↔product_id, observation/participation matrix, proof
honesty, money/currency/validity) and two lookup indexes. No backfill; ImportedProductRow,
ProtectionEvaluation and every runtime path are untouched. downgrade drops ONLY the new table.

Revision ID: mpo1a2b3c4d01
Revises: pev2a2b3c4d01
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mpo1a2b3c4d01"
down_revision: Union[str, None] = "pev2a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_T = "marketplace_price_observations"
_SENTINEL = "__none__"
_MONEY = ("catalog_price", "buyer_price", "seller_promo_price",
          "marketplace_subsidy", "expected_seller_revenue", "commission_base")


def upgrade() -> None:
    op.create_table(
        _T,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ingest_run_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_account_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("external_product_id", sa.String(length=255), nullable=False),
        sa.Column("resolution_status", sa.String(length=12), nullable=False),
        sa.Column("observation_kind", sa.String(length=10), nullable=False),
        sa.Column("promotion_id", sa.String(length=128), nullable=True),
        sa.Column("promotion_key", sa.String(length=128), nullable=False),
        sa.Column("promotion_type", sa.String(length=16), nullable=True),
        sa.Column("participation_status", sa.String(length=20), nullable=True),
        sa.Column("catalog_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("buyer_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("seller_promo_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("marketplace_subsidy", sa.Numeric(18, 2), nullable=True),
        sa.Column("expected_seller_revenue", sa.Numeric(18, 2), nullable=True),
        sa.Column("commission_base", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("currency_status", sa.String(length=8), nullable=False, server_default="unknown"),
        sa.Column("seller_revenue_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("commission_base_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("subsidy_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("provider_dataset", sa.String(length=20), nullable=True),
        sa.Column("external_row_id", sa.String(length=64), nullable=True),
        sa.Column("provider_valid_from", sa.DateTime(), nullable=True),
        sa.Column("provider_valid_to", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_price_obs_store"),
        sa.ForeignKeyConstraint(
            ["marketplace_store_id", "product_id"],
            ["product_placements.marketplace_store_id", "product_placements.product_id"],
            ondelete="CASCADE", name="fk_price_obs_placement"),
        sa.UniqueConstraint("marketplace_store_id", "external_product_id", "promotion_key",
                            "source", "ingest_run_id", name="uq_price_obs_run"),
        sa.CheckConstraint("resolution_status IN ('resolved', 'unassigned')",
                           name="ck_price_obs_resolution_status"),
        sa.CheckConstraint("observation_kind IN ('catalog', 'promotion')", name="ck_price_obs_kind"),
        sa.CheckConstraint(
            "promotion_type IS NULL OR promotion_type IN "
            "('wb_calendar', 'ozon_action', 'ozon_hotsale', 'ozon_individual', 'yandex_promo', 'unknown')",
            name="ck_price_obs_promotion_type"),
        sa.CheckConstraint(
            "participation_status IS NULL OR participation_status IN "
            "('active', 'not_participating', 'ended', 'unknown')",
            name="ck_price_obs_participation"),
        sa.CheckConstraint("currency_status IN ('proven', 'unknown')", name="ck_price_obs_currency_status"),
        sa.CheckConstraint("seller_revenue_status IN ('provider_explicit', 'unknown')",
                           name="ck_price_obs_seller_rev_status"),
        sa.CheckConstraint("commission_base_status IN ('provider_explicit', 'unknown')",
                           name="ck_price_obs_commission_base_status"),
        sa.CheckConstraint("subsidy_status IN ('provider_explicit', 'unknown', 'not_applicable')",
                           name="ck_price_obs_subsidy_status"),
        sa.CheckConstraint("source IN ('api', 'csv', 'manual')", name="ck_price_obs_source"),
        sa.CheckConstraint(
            "(resolution_status = 'resolved' AND product_id IS NOT NULL) OR "
            "(resolution_status = 'unassigned' AND product_id IS NULL)",
            name="ck_price_obs_resolution"),
        sa.CheckConstraint(
            "observation_kind <> 'catalog' OR ("
            "promotion_id IS NULL AND participation_status IS NULL AND promotion_type IS NULL "
            "AND promotion_key = '" + _SENTINEL + "')",
            name="ck_price_obs_catalog"),
        sa.CheckConstraint("observation_kind <> 'promotion' OR participation_status IS NOT NULL",
                           name="ck_price_obs_promotion"),
        sa.CheckConstraint("participation_status IS NULL OR participation_status <> 'active' "
                           "OR promotion_id IS NOT NULL", name="ck_price_obs_active"),
        sa.CheckConstraint(
            "(promotion_id IS NULL AND promotion_key = '" + _SENTINEL + "') OR "
            "(promotion_id IS NOT NULL AND promotion_key = promotion_id)",
            name="ck_price_obs_promotion_key"),
        sa.CheckConstraint("currency_status <> 'proven' OR currency IS NOT NULL",
                           name="ck_price_obs_currency_proven"),
        sa.CheckConstraint(
            "(seller_revenue_status = 'provider_explicit' AND expected_seller_revenue IS NOT NULL) OR "
            "(seller_revenue_status <> 'provider_explicit' AND expected_seller_revenue IS NULL)",
            name="ck_price_obs_seller_rev_value"),
        sa.CheckConstraint(
            "(commission_base_status = 'provider_explicit' AND commission_base IS NOT NULL) OR "
            "(commission_base_status <> 'provider_explicit' AND commission_base IS NULL)",
            name="ck_price_obs_commission_base_value"),
        sa.CheckConstraint("marketplace_subsidy IS NULL OR subsidy_status = 'provider_explicit'",
                           name="ck_price_obs_subsidy_value"),
        sa.CheckConstraint(
            "source = 'api' OR ("
            "currency_status <> 'proven' AND seller_revenue_status <> 'provider_explicit' "
            "AND commission_base_status <> 'provider_explicit' AND subsidy_status <> 'provider_explicit')",
            name="ck_price_obs_provider_source"),
        sa.CheckConstraint(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in _MONEY),
                           name="ck_price_obs_money_nonneg"),
        sa.CheckConstraint("currency IS NULL OR (currency = upper(currency) AND length(currency) = 3)",
                           name="ck_price_obs_currency_fmt"),
        sa.CheckConstraint(
            "provider_valid_to IS NULL OR provider_valid_from IS NULL "
            "OR provider_valid_to >= provider_valid_from",
            name="ck_price_obs_validity"),
    )
    op.create_index("ix_price_obs_latest", _T,
                    ["marketplace_store_id", "product_id", "promotion_key", "fetched_at"])
    op.create_index("ix_price_obs_account", _T, ["marketplace_account_id"])


def downgrade() -> None:
    op.drop_index("ix_price_obs_account", table_name=_T)
    op.drop_index("ix_price_obs_latest", table_name=_T)
    op.drop_table(_T)
