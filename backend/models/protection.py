"""
Loss-promotion protection — schema foundation (PULT-LAUNCH-2.3).

DB-only, feature OFF. No calculation, no gates, no provider, no scheduler, no
executable action here — only tables + constraints. Every invariant that keeps the
feature SAFE is enforced in the database, never merely in a service:

  * a PRODUCT-scoped policy can only reference a product that is REALLY placed in the
    store (composite FK to product_placements) — a foreign or unplaced product is
    impossible at the DB level, and ownership flows Store → Account → Workspace, so
    NO duplicate user_id column is stored that could drift from the store's owner;
  * one store-wide policy per store, one product policy per (store, product);
  * an evaluation snapshot is APPEND-ONLY evidence — absence is NULL, never 0;
  * exactly one ACTIVE action per (store, product, promo) — a NULL promo_id can never
    slip past the uniqueness because promo_key materializes a sentinel.

stop_auto_promotion stays contained (2.1A); this slice adds NO executable path.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Boolean, Integer, Numeric, JSON,
    ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, text,
)
from database import Base

# ── vocabularies (mirrored by CHECK constraints) ────────────────────────────────
TAX_MODES = ("none", "percent", "per_unit")
TAX_APPLIED_TO = ("seller_sale_revenue", "contribution")
COST_CALC_TYPES = ("per_unit", "percent_of_revenue")
EVALUATION_VERDICTS = ("complete", "incomplete", "stale", "conflicting", "unsupported", "unknown")
ACTION_STATES = (
    "detected", "data_check", "incomplete", "stale", "conflicting", "unsupported",
    "eligible", "emergency", "low_margin", "safe", "awaiting_seller",
    "action_requested", "marketplace_processing", "verified_success",
    "rejected", "ambiguous", "reappeared", "cooldown", "manual_attention",
)
# Terminal states — a new active row may coexist with any number of these (history only).
# cooldown is deliberately NOT terminal: a cooling-down row still BLOCKS a second command for the
# same (store, product, promo). Monitoring during cooldown continues via ProtectionEvaluation
# (append-only, no uniqueness), never by creating a second ProtectionActionState.
_ACTION_TERMINAL = ("verified_success", "rejected", "manual_attention")
# Materialized value used for promo_key when the marketplace promo id is unknown, so a
# NULL promo_id can never bypass the active-uniqueness index.
PROMO_KEY_SENTINEL = "__none__"


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class ProtectionPolicy(Base):
    """A seller's loss-protection settings for ONE store, or ONE placed product.

    product_id NULL = store-wide; set = one placed product. The composite FK to
    product_placements is MATCH SIMPLE, so it is skipped when product_id is NULL
    (store-wide) and enforced when it is set — a product policy can only point at a
    real placement of that product in that store, which also pins the same cabinet.
    A plain FK on marketplace_store_id keeps the store valid for the store-wide row.
    """
    __tablename__ = "protection_policies"

    id                   = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_store_id = Column(
        String(36), ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False)
    product_id           = Column(String(36), nullable=True)   # NULL = store-wide

    enabled              = Column(Boolean, nullable=False, default=False, server_default="0")
    emergency_abs        = Column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    emergency_pct        = Column(Numeric(6, 3), nullable=True)
    target_margin_pct    = Column(Numeric(6, 3), nullable=False, default=Decimal("10"), server_default="10")
    # 2.4: default OFF — advertising is only deducted when the seller explicitly opts in AND the
    # ad spend is provably attributed to this product+store; a silent ON would understate cost.
    include_ad_spend     = Column(Boolean, nullable=False, default=False, server_default="0")
    consent_at           = Column(DateTime, nullable=True)
    consent_version      = Column(String(16), nullable=True)
    consent_revoked_at   = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # product-scoped policy → the product MUST be placed in THIS store (same cabinet).
        # MATCH SIMPLE: skipped when product_id IS NULL (store-wide).
        ForeignKeyConstraint(
            ["marketplace_store_id", "product_id"],
            ["product_placements.marketplace_store_id", "product_placements.product_id"],
            ondelete="CASCADE", name="fk_protection_policy_placement",
        ),
        # one product policy per (store, product); NULLs are DISTINCT so store-wide rows
        # are NOT constrained here — they are capped by the partial index below.
        UniqueConstraint("marketplace_store_id", "product_id", name="uq_protection_store_product"),
        # composite-FK target for protection_action_states(policy_id, marketplace_store_id): an
        # action can never bind a policy from one store to a different store.
        UniqueConstraint("id", "marketplace_store_id", name="uq_protection_id_store"),
        # exactly one store-wide policy per store.
        Index("uq_protection_store_wide", "marketplace_store_id", unique=True,
              sqlite_where=text("product_id IS NULL"),
              postgresql_where=text("product_id IS NULL")),
        CheckConstraint("emergency_pct IS NULL OR (emergency_pct >= -100 AND emergency_pct <= 100)",
                        name="ck_protection_emergency_pct_range"),
        CheckConstraint("target_margin_pct >= 0 AND target_margin_pct <= 100",
                        name="ck_protection_target_margin_range"),
        Index("ix_protection_store", "marketplace_store_id"),
    )


class ProtectionTaxSetting(Base):
    """Seller-configured tax for ONE policy (one-to-one). PULT never guesses tax.

    An ABSENT row does NOT mean a confirmed zero — that judgement (and the rule that
    tax_mode='none' counts as confirmed only with seller_confirmed_at) lives in 2.5.
    """
    __tablename__ = "protection_tax_settings"

    id                = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    policy_id         = Column(String(36), ForeignKey("protection_policies.id", ondelete="CASCADE"),
                               nullable=False, unique=True)
    tax_mode          = Column(String(10), nullable=False, default="none", server_default="none")
    tax_rate          = Column(Numeric(6, 3), nullable=True)     # percent, only when tax_mode='percent'
    tax_per_unit      = Column(Numeric(18, 2), nullable=True)    # money, only when tax_mode='per_unit'
    applied_to        = Column(String(24), nullable=False, default="contribution", server_default="contribution")
    effective_from    = Column(DateTime, nullable=True)
    seller_confirmed_at = Column(DateTime, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(_in("tax_mode", TAX_MODES), name="ck_tax_mode"),
        CheckConstraint(_in("applied_to", TAX_APPLIED_TO), name="ck_tax_applied_to"),
        # exactly one of the two value columns is set, matching the mode
        CheckConstraint(
            "(tax_mode = 'none' AND tax_rate IS NULL AND tax_per_unit IS NULL) OR "
            "(tax_mode = 'percent' AND tax_rate IS NOT NULL AND tax_per_unit IS NULL) OR "
            "(tax_mode = 'per_unit' AND tax_per_unit IS NOT NULL AND tax_rate IS NULL)",
            name="ck_tax_mode_matrix"),
        CheckConstraint("tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 100)",
                        name="ck_tax_rate_range"),
        CheckConstraint("tax_per_unit IS NULL OR tax_per_unit >= 0", name="ck_tax_per_unit_nonneg"),
    )


class ProtectionAdditionalCost(Base):
    """A typed seller-declared extra cost line for a policy (never a free-form JSON blob)."""
    __tablename__ = "protection_additional_costs"

    id                = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    policy_id         = Column(String(36), ForeignKey("protection_policies.id", ondelete="CASCADE"),
                               nullable=False)
    name              = Column(String(120), nullable=False)
    amount            = Column(Numeric(18, 2), nullable=False)   # money (per_unit) OR percent value
    calculation_type  = Column(String(20), nullable=False)
    currency          = Column(String(3), nullable=False, default="RUB", server_default="RUB")
    enabled           = Column(Boolean, nullable=False, default=True, server_default="1")
    effective_from    = Column(DateTime, nullable=True)
    seller_confirmed_at = Column(DateTime, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(_in("calculation_type", COST_CALC_TYPES), name="ck_cost_calc_type"),
        CheckConstraint("amount >= 0", name="ck_cost_amount_nonneg"),
        # a percent-of-revenue line is bounded to [0,100]; a per_unit line is any non-negative money
        CheckConstraint(
            "calculation_type = 'per_unit' OR "
            "(calculation_type = 'percent_of_revenue' AND amount <= 100)",
            name="ck_cost_percent_range"),
        CheckConstraint("name <> '' AND name = trim(name)", name="ck_cost_name_clean"),
        CheckConstraint("currency = upper(currency) AND length(currency) = 3", name="ck_cost_currency"),
        Index("ix_cost_policy", "policy_id"),
    )


class ProtectionEvaluation(Base):
    """APPEND-ONLY evidence: the numbers PULT used at one evaluation. Never mutated.

    Absence of a result is stored as NULL (never 0). Ownership/scope is denormalized
    (store_id + product_id) so the evidence survives a policy removal: policy_id is
    SET NULL on policy delete, while a cabinet/store hard-delete cascades the evidence
    with everything else (consistent with the uploads-retention doctrine: cabinet
    deletion cascades rows, but removing one policy must not erase its audit trail).
    product_id is a SOFT reference (no hard FK, like ExecutionLog.decision_id) so the
    recorded figures remain readable through later product churn.
    """
    __tablename__ = "protection_evaluations"

    id                    = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    policy_id             = Column(String(36),
                                   ForeignKey("protection_policies.id", ondelete="SET NULL"),
                                   nullable=True)
    marketplace_store_id  = Column(String(36),
                                   ForeignKey("marketplace_stores.id", ondelete="CASCADE"),
                                   nullable=False)
    product_id            = Column(String(36), nullable=True)   # soft ref (evidence outlives placement)
    projected_contribution = Column(Numeric(18, 2), nullable=True)   # NULL, never 0, when unknown
    contribution_pct      = Column(Numeric(6, 3), nullable=True)
    verdict               = Column(String(16), nullable=False)
    missing_fields        = Column(JSON, nullable=False, default=list)
    reasons               = Column(JSON, nullable=False, default=list)
    inputs_snapshot       = Column(JSON, nullable=False, default=dict)
    evaluated_at          = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(_in("verdict", EVALUATION_VERDICTS), name="ck_evaluation_verdict"),
        Index("ix_evaluation_policy", "policy_id"),
        Index("ix_evaluation_store_time", "marketplace_store_id", "evaluated_at"),
        Index("ix_evaluation_product", "product_id"),
    )


class ProtectionActionState(Base):
    """The lifecycle of ONE protection action for a (store, product, promo).

    promo_key materializes the promo id (or a sentinel when unknown), so the partial
    unique index guarantees exactly one ACTIVE (non-terminal, non-cooldown) row per
    (store, product, promo) even when promo_id is NULL. Terminal rows and cooldown
    rows are exempt from the index, so history is kept and monitoring may continue
    during cooldown without blocking future detections. Execution itself flows through
    the existing marketplace_executor / ExecutionLog — this row only tracks state.
    No automatic revert / re-entry into a promotion is modelled.
    """
    __tablename__ = "protection_action_states"

    id                   = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # policy_id + marketplace_store_id feed ONE composite FK to the policy, and
    # marketplace_store_id + product_id feed ANOTHER to product_placements — so the action's store
    # MUST equal its policy's store AND the (store, product) MUST be a real placement (same cabinet).
    # Three independent per-column FKs could not express that: they would allow a policy from store A,
    # a store_id of store B and a product from store C.
    policy_id            = Column(String(36), nullable=False)
    marketplace_store_id = Column(String(36), nullable=False)
    product_id           = Column(String(36), nullable=False)
    promo_id             = Column(String(128), nullable=True)
    promo_key            = Column(String(128), nullable=False)   # promo_id or PROMO_KEY_SENTINEL
    state                = Column(String(24), nullable=False, default="detected", server_default="detected")
    idempotency_key      = Column(String(160), nullable=False, unique=True)
    execution_log_id     = Column(String(36),
                                  ForeignKey("execution_logs.id", ondelete="SET NULL"), nullable=True)
    verify_scheduled_at  = Column(DateTime, nullable=True)
    cooldown_until       = Column(DateTime, nullable=True)
    reappear_count       = Column(Integer, nullable=False, default=0, server_default="0")
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # action.store == policy.store (composite FK to the policy's (id, store) unique key)
        ForeignKeyConstraint(
            ["policy_id", "marketplace_store_id"],
            ["protection_policies.id", "protection_policies.marketplace_store_id"],
            ondelete="CASCADE", name="fk_action_policy_store"),
        # action.(store, product) is a REAL placement (same cabinet, placed) — foreign store,
        # other cabinet, or unplaced product are all rejected by the DB
        ForeignKeyConstraint(
            ["marketplace_store_id", "product_id"],
            ["product_placements.marketplace_store_id", "product_placements.product_id"],
            ondelete="CASCADE", name="fk_action_placement"),
        CheckConstraint(_in("state", ACTION_STATES), name="ck_action_state"),
        CheckConstraint("reappear_count >= 0", name="ck_action_reappear_nonneg"),
        # promo_key is always the materialized promo_id, or the sentinel when promo_id is NULL —
        # this is what stops a NULL promo_id from bypassing the active-uniqueness index.
        CheckConstraint(
            "(promo_id IS NOT NULL AND promo_key = promo_id) OR "
            f"(promo_id IS NULL AND promo_key = '{PROMO_KEY_SENTINEL}')",
            name="ck_action_promo_key"),
        # at most one ACTIVE (in-flight) action per (store, product, promo); terminal/cooldown rows
        # are exempt, so history is kept and monitoring continues during cooldown.
        Index("uq_protection_action_active", "marketplace_store_id", "product_id", "promo_key",
              unique=True,
              sqlite_where=text("state NOT IN (" + ", ".join(f"'{s}'" for s in _ACTION_TERMINAL) + ")"),
              postgresql_where=text("state NOT IN (" + ", ".join(f"'{s}'" for s in _ACTION_TERMINAL) + ")")),
        Index("ix_action_policy", "policy_id"),
        Index("ix_action_store_product", "marketplace_store_id", "product_id"),
    )
