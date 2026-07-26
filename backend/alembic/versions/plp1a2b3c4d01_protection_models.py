"""PULT-LAUNCH-2.3 — loss-promotion protection schema (feature OFF)

Adds five additive tables + constraints, NO data change, NO runtime path:
  * protection_policies      — seller settings per store / placed product; product-scoped rows
                               carry a composite FK to product_placements so a foreign or unplaced
                               product is impossible at the DB level. Ownership flows
                               Store → Account → Workspace (no duplicate user_id).
  * protection_tax_settings  — one-to-one seller-configured tax (CHECK matrix).
  * protection_additional_costs — typed extra-cost lines (per_unit | percent_of_revenue).
  * protection_evaluations   — append-only evidence snapshot (absence = NULL, never 0).
  * protection_action_states — action lifecycle; one ACTIVE row per (store, product, promo)
                               via a materialized promo_key + partial unique index.

stop_auto_promotion stays contained; this slice adds no executable action.

Revision ID: plp1a2b3c4d01
Revises: sdp1a2b3c4d01
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "plp1a2b3c4d01"
down_revision: Union[str, None] = "sdp1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICY = "protection_policies"
_TAX = "protection_tax_settings"
_COST = "protection_additional_costs"
_EVAL = "protection_evaluations"
_ACTION = "protection_action_states"

_ACTION_STATES = (
    "detected", "data_check", "incomplete", "stale", "conflicting", "unsupported",
    "eligible", "emergency", "low_margin", "safe", "awaiting_seller",
    "action_requested", "marketplace_processing", "verified_success",
    "rejected", "ambiguous", "reappeared", "cooldown", "manual_attention",
)
_ACTION_TERMINAL = ("verified_success", "rejected", "manual_attention", "cooldown")
_PROMO_SENTINEL = "__none__"


def _quoted(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # 1. protection_policies
    op.create_table(
        _POLICY,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("emergency_abs", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("emergency_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("target_margin_pct", sa.Numeric(6, 3), nullable=False, server_default="10"),
        sa.Column("include_ad_spend", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("consent_at", sa.DateTime(), nullable=True),
        sa.Column("consent_version", sa.String(length=16), nullable=True),
        sa.Column("consent_revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["marketplace_store_id", "product_id"],
            ["product_placements.marketplace_store_id", "product_placements.product_id"],
            ondelete="CASCADE", name="fk_protection_policy_placement"),
        sa.UniqueConstraint("marketplace_store_id", "product_id", name="uq_protection_store_product"),
        sa.CheckConstraint("emergency_pct IS NULL OR (emergency_pct >= -100 AND emergency_pct <= 100)",
                           name="ck_protection_emergency_pct_range"),
        sa.CheckConstraint("target_margin_pct >= 0 AND target_margin_pct <= 100",
                           name="ck_protection_target_margin_range"),
    )
    op.create_index("uq_protection_store_wide", _POLICY, ["marketplace_store_id"], unique=True,
                    sqlite_where=sa.text("product_id IS NULL"),
                    postgresql_where=sa.text("product_id IS NULL"))
    op.create_index("ix_protection_store", _POLICY, ["marketplace_store_id"])

    # 2. protection_tax_settings (one-to-one)
    op.create_table(
        _TAX,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36),
                  sa.ForeignKey("protection_policies.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("tax_mode", sa.String(length=10), nullable=False, server_default="none"),
        sa.Column("tax_rate", sa.Numeric(6, 3), nullable=True),
        sa.Column("tax_per_unit", sa.Numeric(18, 2), nullable=True),
        sa.Column("applied_to", sa.String(length=24), nullable=False, server_default="contribution"),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("seller_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("tax_mode IN ('none','percent','per_unit')", name="ck_tax_mode"),
        sa.CheckConstraint("applied_to IN ('seller_sale_revenue','contribution')", name="ck_tax_applied_to"),
        sa.CheckConstraint(
            "(tax_mode = 'none' AND tax_rate IS NULL AND tax_per_unit IS NULL) OR "
            "(tax_mode = 'percent' AND tax_rate IS NOT NULL AND tax_per_unit IS NULL) OR "
            "(tax_mode = 'per_unit' AND tax_per_unit IS NOT NULL AND tax_rate IS NULL)",
            name="ck_tax_mode_matrix"),
        sa.CheckConstraint("tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 100)", name="ck_tax_rate_range"),
        sa.CheckConstraint("tax_per_unit IS NULL OR tax_per_unit >= 0", name="ck_tax_per_unit_nonneg"),
    )

    # 3. protection_additional_costs
    op.create_table(
        _COST,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36),
                  sa.ForeignKey("protection_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("calculation_type", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="RUB"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("seller_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("calculation_type IN ('per_unit','percent_of_revenue')", name="ck_cost_calc_type"),
        sa.CheckConstraint("amount >= 0", name="ck_cost_amount_nonneg"),
        sa.CheckConstraint(
            "calculation_type = 'per_unit' OR "
            "(calculation_type = 'percent_of_revenue' AND amount <= 100)",
            name="ck_cost_percent_range"),
        sa.CheckConstraint("name <> '' AND name = trim(name)", name="ck_cost_name_clean"),
        sa.CheckConstraint("currency = upper(currency) AND length(currency) = 3", name="ck_cost_currency"),
    )
    op.create_index("ix_cost_policy", _COST, ["policy_id"])

    # 4. protection_evaluations (append-only evidence)
    op.create_table(
        _EVAL,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36),
                  sa.ForeignKey("protection_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("projected_contribution", sa.Numeric(18, 2), nullable=True),
        sa.Column("contribution_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("inputs_snapshot", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('complete','incomplete','stale','conflicting','unsupported','unknown')",
            name="ck_evaluation_verdict"),
    )
    op.create_index("ix_evaluation_policy", _EVAL, ["policy_id"])
    op.create_index("ix_evaluation_store_time", _EVAL, ["marketplace_store_id", "evaluated_at"])
    op.create_index("ix_evaluation_product", _EVAL, ["product_id"])

    # 5. protection_action_states
    op.create_table(
        _ACTION,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36),
                  sa.ForeignKey("protection_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36),
                  sa.ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("promo_id", sa.String(length=128), nullable=True),
        sa.Column("promo_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="detected"),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("execution_log_id", sa.String(length=36),
                  sa.ForeignKey("execution_logs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("verify_scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("reappear_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(f"state IN ({_quoted(_ACTION_STATES)})", name="ck_action_state"),
        sa.CheckConstraint("reappear_count >= 0", name="ck_action_reappear_nonneg"),
        sa.CheckConstraint(
            "(promo_id IS NOT NULL AND promo_key = promo_id) OR "
            f"(promo_id IS NULL AND promo_key = '{_PROMO_SENTINEL}')",
            name="ck_action_promo_key"),
    )
    _active = f"state NOT IN ({_quoted(_ACTION_TERMINAL)})"
    op.create_index("uq_protection_action_active", _ACTION,
                    ["marketplace_store_id", "product_id", "promo_key"], unique=True,
                    sqlite_where=sa.text(_active), postgresql_where=sa.text(_active))
    op.create_index("ix_action_policy", _ACTION, ["policy_id"])
    op.create_index("ix_action_store_product", _ACTION, ["marketplace_store_id", "product_id"])


def downgrade() -> None:
    # drop children first (FK order); each drop_table removes its own indexes/constraints
    op.drop_table(_ACTION)
    op.drop_table(_EVAL)
    op.drop_table(_COST)
    op.drop_table(_TAX)
    op.drop_table(_POLICY)
