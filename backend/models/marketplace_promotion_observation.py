"""PULT-LAUNCH-2.5D-Yandex-B3 — account-level promotion participation evidence (feature OFF).

Yandex promotions are a BUSINESS-level fact: a promo participation status (AUTO / PARTIALLY_AUTO /
MANUAL / NOT_PARTICIPATING / …) belongs to the cabinet (businessId), NOT to one campaign store. The
promo API only ever names specific campaigns for PARTIALLY_AUTO (`autoParticipatingDetails.campaignIds`);
AUTO says "all stores where the product is sold" WITHOUT listing them, and MANUAL gives no campaigns at
all. So a store-scoped MarketplacePriceObservation (store_id NOT NULL) cannot honestly hold these facts.

This pair of APPEND-ONLY, IMMUTABLE tables stores the evidence at the level the API actually proves it:

  * MarketplacePromotionObservation (PARENT) — one row per (account, offer, promo, run). Carries the
    verbatim provider_status, a normalized participation_status/auto_participation/attribution_status,
    and the promo prices. NO seller revenue / subsidy / commission / profit — a promo price is a
    buyer-facing price, never an economic verdict.
  * MarketplacePromotionStoreEvidence (CHILD) — the PROVEN campaignIds of a PARTIALLY_AUTO row, each
    mapped to a real MarketplaceStore of this cabinet or kept as `unmapped` when the campaign is not
    (yet) modelled in PULT. A campaign is NEVER auto-created, and a store of another account is
    impossible at the DB level.

Every SAFETY invariant that CAN live in the database does (composite FKs, CHECK, UNIQUE). The one
invariant a portable CHECK cannot express — "a PARTIALLY_AUTO parent has ≥1 child" — is a TRANSACTIONAL
WRITER invariant (the writer inserts parent + all children in one transaction, or none), not a DB one.

Schema/provenance ONLY in this slice: the writer is added separately, feature-flagged OFF. No token,
no raw payload, no PII, no businessId/campaignId/offerId in any log.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Numeric, Boolean, JSON, text,
    ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index,
)
from database import Base

# ── normalized vocabularies (mirrored by CHECK constraints) ─────────────────────
RESOLUTION_STATUSES = ("resolved", "unassigned")
PARTICIPATION_STATUSES = ("active", "not_participating", "unknown")
ATTRIBUTION_STATUSES = ("account_wide", "exact_stores", "unresolved", "unmapped_stores")
CURRENCY_STATUSES = ("proven", "unknown")
MAPPING_STATUSES = ("mapped", "unmapped")
# Fixed provenance for THIS model — it is a Yandex API promotion evidence table and nothing else.
MARKETPLACE = "yandex"
SOURCE = "api"
PROVIDER_DATASET = "promos"
PROMOTION_TYPE = "yandex_promo"
# The three promo money components (each nullable Decimal; NULL = unknown, never 0).
_MONEY_COLS = ("pre_promo_price", "promo_buyer_price", "promo_max_price")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class MarketplacePromotionObservation(Base):
    __tablename__ = "marketplace_promotion_observations"

    id                     = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ingest_run_id          = Column(String(36), nullable=False)   # one value per sync pass

    marketplace_account_id = Column(String(36), nullable=False)
    marketplace            = Column(String(20), nullable=False)
    product_id             = Column(String(36), nullable=True)    # NULL only for an unassigned row
    external_product_id    = Column(String(255), nullable=False)  # offerId — always kept
    resolution_status      = Column(String(12), nullable=False)   # resolved | unassigned

    promotion_id           = Column(String(128), nullable=False)  # promoId — always present
    promotion_type         = Column(String(16), nullable=False)   # fixed yandex_promo

    # Verbatim provider status (NOT an enum — a new Yandex status is kept as-is, never coerced).
    provider_status        = Column(String(64), nullable=False)
    participation_status   = Column(String(20), nullable=False)   # active | not_participating | unknown
    auto_participation     = Column(Boolean, nullable=True)       # true AUTO/PARTIALLY_AUTO, false MANUAL, NULL unknown
    attribution_status     = Column(String(16), nullable=False)   # account_wide | exact_stores | unresolved | unmapped_stores

    pre_promo_price        = Column(Numeric(18, 2), nullable=True)  # discountParams.price (зачёркнутая)
    promo_buyer_price      = Column(Numeric(18, 2), nullable=True)  # discountParams.promoPrice
    promo_max_price        = Column(Numeric(18, 2), nullable=True)  # discountParams.maxPromoPrice (порог участия)
    currency               = Column(String(3), nullable=True)
    currency_status        = Column(String(8), nullable=False, default="unknown", server_default="unknown")

    source                 = Column(String(10), nullable=False)   # fixed api
    provider_dataset       = Column(String(20), nullable=False)   # fixed promos
    promotion_start_at     = Column(DateTime, nullable=True)
    promotion_end_at       = Column(DateTime, nullable=True)
    fetched_at             = Column(DateTime, nullable=False)
    # PULT-LAUNCH-2.5E-1 — change-only bookkeeping (feature OFF). last_verified_at is the latest run
    # that re-observed this EXACT evidence (incl. the child campaign set); evidence_fingerprint is the
    # SHA-256 of the parent semantic fields + the sorted child set (NULL → change-only inserts).
    last_verified_at       = Column(DateTime(timezone=True), nullable=False)
    evidence_fingerprint   = Column(String(64), nullable=True)
    missing_fields         = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    created_at             = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # ── tenant isolation ────────────────────────────────────────────────────────────────────
        # cabinet + marketplace pin: a yandex-promo row can only reference a yandex cabinet; account
        # (privacy) delete cascades the evidence.
        ForeignKeyConstraint(
            ["marketplace_account_id", "marketplace"],
            ["marketplace_accounts.id", "marketplace_accounts.marketplace"],
            ondelete="CASCADE", name="fk_promo_obs_account"),
        # a resolved row references a REAL product of THIS cabinet (composite FK, MATCH SIMPLE: skipped
        # when product_id IS NULL). A product of another account is impossible at the DB level.
        ForeignKeyConstraint(
            ["product_id", "marketplace_account_id"],
            ["products.id", "products.marketplace_account_id"],
            ondelete="CASCADE", name="fk_promo_obs_product"),

        # ── idempotency: one row per (account, offer, promo, source, run) — every key part NOT NULL ─
        UniqueConstraint("marketplace_account_id", "external_product_id", "promotion_id",
                         "source", "ingest_run_id", name="uq_promo_obs_run"),

        # ── fixed provenance (this model is api / promos / yandex_promo only) ─────────────────────
        CheckConstraint("marketplace = 'yandex'", name="ck_promo_obs_marketplace"),
        CheckConstraint("source = 'api'", name="ck_promo_obs_source"),
        CheckConstraint("provider_dataset = 'promos'", name="ck_promo_obs_dataset"),
        CheckConstraint("promotion_type = 'yandex_promo'", name="ck_promo_obs_promotion_type"),

        # ── provider_status: PORTABLE only (never an enum of Yandex values) ──────────────────────
        CheckConstraint(
            "provider_status <> '' AND provider_status = trim(provider_status) "
            "AND length(provider_status) <= 64", name="ck_promo_obs_provider_status_clean"),

        # ── normalized dictionaries ──────────────────────────────────────────────────────────────
        CheckConstraint(_in("resolution_status", RESOLUTION_STATUSES), name="ck_promo_obs_resolution_status"),
        CheckConstraint(_in("participation_status", PARTICIPATION_STATUSES), name="ck_promo_obs_participation"),
        CheckConstraint(_in("attribution_status", ATTRIBUTION_STATUSES), name="ck_promo_obs_attribution"),
        CheckConstraint(_in("currency_status", CURRENCY_STATUSES), name="ck_promo_obs_currency_status"),

        # ── resolution ↔ product_id (strict: closes the MATCH SIMPLE hole both ways) ─────────────
        CheckConstraint(
            "(resolution_status = 'resolved' AND product_id IS NOT NULL) OR "
            "(resolution_status = 'unassigned' AND product_id IS NULL)",
            name="ck_promo_obs_resolution"),

        # ── exact_stores is ONLY reachable from PARTIALLY_AUTO (its children carry the campaignIds).
        # The DB does NOT prove ≥1 child exists — that is the transactional writer invariant.
        CheckConstraint("attribution_status <> 'exact_stores' OR provider_status = 'PARTIALLY_AUTO'",
                        name="ck_promo_obs_exact_stores"),

        # ── money / currency / period ────────────────────────────────────────────────────────────
        CheckConstraint(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in _MONEY_COLS),
                        name="ck_promo_obs_money_nonneg"),
        CheckConstraint("currency IS NULL OR (currency = upper(currency) AND length(currency) = 3)",
                        name="ck_promo_obs_currency_fmt"),
        CheckConstraint("currency_status <> 'proven' OR currency IS NOT NULL",
                        name="ck_promo_obs_currency_proven"),
        CheckConstraint(
            "promotion_end_at IS NULL OR promotion_start_at IS NULL "
            "OR promotion_end_at >= promotion_start_at", name="ck_promo_obs_period"),

        # ── lookups (retention/cleanup + latest) ─────────────────────────────────────────────────
        Index("ix_promo_obs_account_time", "marketplace_account_id", "fetched_at"),
        Index("ix_promo_obs_latest", "marketplace_account_id", "product_id", "promotion_id", "fetched_at"),
        Index("ix_promo_obs_promotion", "promotion_id"),
        # PULT-LAUNCH-2.5E-1 — change-only latest-of-series lookup, keyed by the SERIES identity
        # (account, external_product_id, promotion_id, source) with fetched_at last for MAX.
        # last_verified_at / evidence_fingerprint are NOT indexed (fingerprint compared, not searched).
        Index("ix_promo_obs_series", "marketplace_account_id", "external_product_id",
              "promotion_id", "source", "fetched_at"),
    )


class MarketplacePromotionStoreEvidence(Base):
    __tablename__ = "marketplace_promotion_store_evidence"

    id                        = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    promotion_observation_id  = Column(String(36),
                                       ForeignKey("marketplace_promotion_observations.id", ondelete="CASCADE"),
                                       nullable=False)
    marketplace_account_id    = Column(String(36), nullable=False)
    external_store_id         = Column(String(128), nullable=False)   # campaignId — always kept as evidence
    marketplace_store_id      = Column(String(36), nullable=True)     # set only when the campaign maps to a store
    mapping_status            = Column(String(10), nullable=False)    # mapped | unmapped
    created_at                = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # a mapped campaign references a REAL store of THIS cabinet (composite FK, MATCH SIMPLE:
        # skipped when store_id IS NULL). A store of another account is impossible at the DB level.
        ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_promo_store_ev_store"),

        UniqueConstraint("promotion_observation_id", "external_store_id", name="uq_promo_store_ev"),

        CheckConstraint(_in("mapping_status", MAPPING_STATUSES), name="ck_promo_store_ev_mapping_vocab"),
        CheckConstraint(
            "(mapping_status = 'mapped' AND marketplace_store_id IS NOT NULL) OR "
            "(mapping_status = 'unmapped' AND marketplace_store_id IS NULL)",
            name="ck_promo_store_ev_mapping"),
        CheckConstraint(
            "external_store_id <> '' AND external_store_id = trim(external_store_id) "
            "AND external_store_id NOT LIKE '% %'", name="ck_promo_store_ev_external_clean"),

        Index("ix_promo_store_ev_account_ext", "marketplace_account_id", "external_store_id"),
    )
