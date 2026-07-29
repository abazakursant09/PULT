"""
PULT-LAUNCH-2.5C — proven price/currency storage for loss-promotion protection (feature OFF).

An APPEND-ONLY, IMMUTABLE observation of a product's price state at one fetch, distinct from the
generic `ImportedProductRow` (whose single Float `price` has marketplace-dependent meaning). Every
economic component is a separate, nullable Decimal with an explicit PROOF status, so a buyer-facing
price is never silently taken as the seller's revenue and an unproven value can never authorise an
executable action.

Schema/provenance ONLY: NO provider ingest, NO runtime wiring, NO scheduler. Append-only is
guaranteed in this slice by the ABSENCE of any runtime writer — no ORM hook, no DB trigger. A future
fetch-run writes a NEW row; old rows are never rewritten (a promo ending is a new observation, not a
mutation of the active one). Reproducibility of a ProtectionEvaluation comes from the values copied
into its inputs_snapshot plus a soft observation-id pointer — not from this row surviving.

Every SAFETY invariant lives in the DATABASE (CHECK / composite FK / UNIQUE), never merely a service:
  * tenant isolation — a resolved observation can only reference a REAL placement of that product in
    that store (composite FK to product_placements), and the store must belong to the same cabinet
    (composite FK to marketplace_stores); cross-account store/product are impossible at the DB level;
  * a resolution_status='resolved' row MUST carry a product_id (the placement FK is MATCH SIMPLE and
    is skipped when product_id IS NULL, so the CHECK closes that hole);
  * a proof status is honest — provider_explicit/proven requires the value AND source='api', so a
    CSV/manual observation can never be executable;
  * marketplace_subsidy is never derived by subtracting two prices — it needs an explicit status;
  * idempotency by (store, external product, promotion, source, fetch-run) — a stable provider id is
    NOT a bar to a new price; external_row_id is provenance only.

No raw payload, no credentials, no PII, no token is stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Numeric, Boolean, JSON, text,
    ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index,
)
from database import Base

# ── vocabularies (mirrored by CHECK constraints) ────────────────────────────────
RESOLUTION_STATUSES = ("resolved", "unassigned")
OBSERVATION_KINDS = ("catalog", "promotion")
PROMOTION_TYPES = ("wb_calendar", "ozon_action", "ozon_hotsale", "ozon_individual", "yandex_promo", "unknown")
PARTICIPATION_STATUSES = ("active", "not_participating", "ended", "unknown")
CURRENCY_STATUSES = ("proven", "unknown")
PROVIDER_PROOF_STATUSES = ("provider_explicit", "unknown")          # seller_revenue / commission_base
SUBSIDY_STATUSES = ("provider_explicit", "unknown", "not_applicable")
SOURCES = ("api", "csv", "manual")
# Materialized value for promotion_key when promotion_id is NULL, so the run-uniqueness index is
# never bypassed by a NULL (same trick as ProtectionActionState.promo_key).
PROMO_KEY_SENTINEL = "__none__"
# The six economic money components (each an independent, nullable Decimal; NULL = unknown, never 0).
_MONEY_COLS = ("catalog_price", "buyer_price", "seller_promo_price",
               "marketplace_subsidy", "expected_seller_revenue", "commission_base")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class MarketplacePriceObservation(Base):
    __tablename__ = "marketplace_price_observations"

    id                     = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # fetch/run identity — one value per sync run; distinct from the provider's stable product id.
    ingest_run_id          = Column(String(36), nullable=False)

    marketplace_account_id = Column(String(36), nullable=False)
    marketplace_store_id   = Column(String(36), nullable=False)
    product_id             = Column(String(36), nullable=True)      # NULL only for an unassigned row
    external_product_id    = Column(String(255), nullable=False)    # provider product id (nmID/product_id/offerId)
    resolution_status      = Column(String(12), nullable=False)     # resolved | unassigned

    observation_kind       = Column(String(10), nullable=False)     # catalog | promotion
    promotion_id           = Column(String(128), nullable=True)
    promotion_key          = Column(String(128), nullable=False)    # promotion_id or PROMO_KEY_SENTINEL
    promotion_type         = Column(String(16), nullable=True)      # promotion observation only
    participation_status   = Column(String(20), nullable=True)      # promotion observation only

    catalog_price           = Column(Numeric(18, 2), nullable=True)
    buyer_price             = Column(Numeric(18, 2), nullable=True)
    seller_promo_price      = Column(Numeric(18, 2), nullable=True)
    marketplace_subsidy     = Column(Numeric(18, 2), nullable=True)  # never derived by subtraction
    expected_seller_revenue = Column(Numeric(18, 2), nullable=True)  # provider-only
    commission_base         = Column(Numeric(18, 2), nullable=True)

    # PULT-LAUNCH-2.5D — two raw, PROVEN Ozon /v5/product/info/prices fields. Each stands alone:
    #   * provider_min_price is the provider's declared `min_price` verbatim — NOT the seller's
    #     revenue, NOT a promo/marketing price, NOT a margin, NOT an emergency floor. It is evidence,
    #     never a computed economic verdict.
    #   * auto_action_enabled is the provider's declared auto-promotion flag. NULL means Ozon gave no
    #     proven value; NULL is never coerced to False (absence of proof ≠ proof of absence).
    provider_min_price      = Column(Numeric(18, 2), nullable=True)  # raw Ozon min_price; 0 is a real value
    auto_action_enabled     = Column(Boolean, nullable=True)         # NULL = provider did not prove it

    # PULT-LAUNCH-2.5D-WB — WB Club (WB Кошелёк) buyer-facing price, a SEPARATE proven buyer price for
    # club members (clubDiscountedPrice). It is a buyer price, NOT the seller's revenue; the gap vs
    # buyer_price is NEVER treated as a subsidy (funding is not proven by the API). 0 is a real value.
    club_buyer_price        = Column(Numeric(18, 2), nullable=True)

    currency               = Column(String(3), nullable=True)       # no default; proven from provider only
    currency_status        = Column(String(8),  nullable=False, default="unknown", server_default="unknown")
    seller_revenue_status  = Column(String(24), nullable=False, default="unknown", server_default="unknown")
    commission_base_status = Column(String(24), nullable=False, default="unknown", server_default="unknown")
    subsidy_status         = Column(String(24), nullable=False, default="unknown", server_default="unknown")

    source                 = Column(String(10), nullable=False)     # api | csv | manual
    provider_dataset       = Column(String(20), nullable=True)      # provenance
    external_row_id        = Column(String(64), nullable=True)      # provenance ONLY — never a uniqueness lever
    provider_valid_from    = Column(DateTime, nullable=True)        # provider-declared promo window
    provider_valid_to      = Column(DateTime, nullable=True)
    fetched_at             = Column(DateTime, nullable=False)       # when PULT observed the state
    # PULT-LAUNCH-2.5E-1 — change-only bookkeeping (feature OFF). last_verified_at is the latest run
    # that re-observed this EXACT evidence (fetched_at stays pinned to first-observed); evidence_
    # fingerprint is the SHA-256 of the semantic fields (NULL only on hypothetical pre-fingerprint
    # rows → change-only treats NULL as "always differ" and inserts, never a false dedupe).
    last_verified_at       = Column(DateTime(timezone=True), nullable=False)
    evidence_fingerprint   = Column(String(64), nullable=True)
    # DB default '[]' (portable: SQLite stores the text, PostgreSQL casts the literal to json), so a
    # raw INSERT omitting the column still yields []. Python `default=list` (a callable, never a shared
    # literal) gives each ORM row its own list.
    missing_fields         = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    created_at             = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # ── tenant isolation (composite FKs, MATCH SIMPLE) ──────────────────────────────────────
        # store belongs to the cabinet; store hard-delete / cabinet privacy-delete cascade the evidence
        ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_price_obs_store"),
        # a RESOLVED observation must reference a REAL placement (same store, same cabinet). MATCH
        # SIMPLE: skipped when product_id IS NULL (unassigned); the CHECK below forbids resolved+NULL.
        ForeignKeyConstraint(
            ["marketplace_store_id", "product_id"],
            ["product_placements.marketplace_store_id", "product_placements.product_id"],
            ondelete="CASCADE", name="fk_price_obs_placement"),

        # ── idempotency: one row per (store, external product, KIND, promotion, source, fetch-run) ─
        # observation_kind is IN the key so a single run may carry BOTH a catalog observation and a
        # promotion observation with promotion_id NULL (both promotion_key='__none__') without collision.
        UniqueConstraint("marketplace_store_id", "external_product_id", "observation_kind",
                         "promotion_key", "source", "ingest_run_id", name="uq_price_obs_run"),

        # ── enum dictionaries ───────────────────────────────────────────────────────────────────
        CheckConstraint(_in("resolution_status", RESOLUTION_STATUSES), name="ck_price_obs_resolution_status"),
        CheckConstraint(_in("observation_kind", OBSERVATION_KINDS), name="ck_price_obs_kind"),
        CheckConstraint(f"promotion_type IS NULL OR {_in('promotion_type', PROMOTION_TYPES)}",
                        name="ck_price_obs_promotion_type"),
        CheckConstraint(f"participation_status IS NULL OR {_in('participation_status', PARTICIPATION_STATUSES)}",
                        name="ck_price_obs_participation"),
        CheckConstraint(_in("currency_status", CURRENCY_STATUSES), name="ck_price_obs_currency_status"),
        CheckConstraint(_in("seller_revenue_status", PROVIDER_PROOF_STATUSES), name="ck_price_obs_seller_rev_status"),
        CheckConstraint(_in("commission_base_status", PROVIDER_PROOF_STATUSES), name="ck_price_obs_commission_base_status"),
        CheckConstraint(_in("subsidy_status", SUBSIDY_STATUSES), name="ck_price_obs_subsidy_status"),
        CheckConstraint(_in("source", SOURCES), name="ck_price_obs_source"),

        # ── resolution ↔ product_id (closes the MATCH SIMPLE hole) ──────────────────────────────
        CheckConstraint(
            "(resolution_status = 'resolved' AND product_id IS NOT NULL) OR "
            "(resolution_status = 'unassigned' AND product_id IS NULL)",
            name="ck_price_obs_resolution"),

        # ── observation-kind / participation matrix ─────────────────────────────────────────────
        # catalog NEVER proves promo-absence: it just carries no promo fields.
        CheckConstraint(
            "observation_kind <> 'catalog' OR ("
            "promotion_id IS NULL AND participation_status IS NULL AND promotion_type IS NULL "
            "AND promotion_key = '" + PROMO_KEY_SENTINEL + "')",
            name="ck_price_obs_catalog"),
        CheckConstraint("observation_kind <> 'promotion' OR "
                        "(participation_status IS NOT NULL AND promotion_type IS NOT NULL)",
                        name="ck_price_obs_promotion"),
        CheckConstraint("participation_status IS NULL OR participation_status <> 'active' "
                        "OR promotion_id IS NOT NULL", name="ck_price_obs_active"),
        CheckConstraint(
            "(promotion_id IS NULL AND promotion_key = '" + PROMO_KEY_SENTINEL + "') OR "
            "(promotion_id IS NOT NULL AND promotion_key = promotion_id)",
            name="ck_price_obs_promotion_key"),

        # ── proof honesty ───────────────────────────────────────────────────────────────────────
        CheckConstraint("currency_status <> 'proven' OR currency IS NOT NULL",
                        name="ck_price_obs_currency_proven"),
        # provider_explicit ↔ value present (both directions): no proven-without-value, no value-without-proof
        CheckConstraint(
            "(seller_revenue_status = 'provider_explicit' AND expected_seller_revenue IS NOT NULL) OR "
            "(seller_revenue_status <> 'provider_explicit' AND expected_seller_revenue IS NULL)",
            name="ck_price_obs_seller_rev_value"),
        CheckConstraint(
            "(commission_base_status = 'provider_explicit' AND commission_base IS NOT NULL) OR "
            "(commission_base_status <> 'provider_explicit' AND commission_base IS NULL)",
            name="ck_price_obs_commission_base_value"),
        # biconditional: provider_explicit ⇔ a value (0 is a PROVEN value, so provider_explicit + 0 is
        # allowed); provider_explicit + NULL and value + unknown/not_applicable are both rejected.
        CheckConstraint(
            "(subsidy_status = 'provider_explicit' AND marketplace_subsidy IS NOT NULL) OR "
            "(subsidy_status <> 'provider_explicit' AND marketplace_subsidy IS NULL)",
            name="ck_price_obs_subsidy_value"),
        # any proven/provider_explicit proof is allowed ONLY on an API observation → CSV/manual can
        # never be executable.
        CheckConstraint(
            "source = 'api' OR ("
            "currency_status <> 'proven' AND seller_revenue_status <> 'provider_explicit' "
            "AND commission_base_status <> 'provider_explicit' AND subsidy_status <> 'provider_explicit')",
            name="ck_price_obs_provider_source"),

        # ── money / currency / validity ─────────────────────────────────────────────────────────
        CheckConstraint(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in _MONEY_COLS),
                        name="ck_price_obs_money_nonneg"),
        CheckConstraint("club_buyer_price IS NULL OR club_buyer_price >= 0",
                        name="ck_price_obs_club_nonneg"),
        CheckConstraint("currency IS NULL OR (currency = upper(currency) AND length(currency) = 3)",
                        name="ck_price_obs_currency_fmt"),
        CheckConstraint(
            "provider_valid_to IS NULL OR provider_valid_from IS NULL "
            "OR provider_valid_to >= provider_valid_from",
            name="ck_price_obs_validity"),

        # ── lookups ─────────────────────────────────────────────────────────────────────────────
        Index("ix_price_obs_latest", "marketplace_store_id", "product_id", "promotion_key", "fetched_at"),
        Index("ix_price_obs_account", "marketplace_account_id"),
        # PULT-LAUNCH-2.5E-1 — change-only latest-of-series lookup, keyed by the SERIES identity
        # (external_product_id, not the resolved internal product_id; observation_kind + source
        # included) with fetched_at last so MAX is a range scan. last_verified_at / evidence_
        # fingerprint are deliberately NOT indexed (the fingerprint is compared, never searched).
        Index("ix_price_obs_series", "marketplace_store_id", "external_product_id",
              "observation_kind", "promotion_key", "source", "fetched_at"),
        # PULT-LAUNCH-2.5E-2B-1 — per-account age scan for the future retention sweep (keyed by
        # fetched_at, the evidence time, NOT created_at). Mirrors the promotion table's existing
        # ix_promo_obs_account_time. Schema only; nothing prunes any row yet.
        Index("ix_price_obs_account_time", "marketplace_account_id", "fetched_at"),
    )
