"""
PULT-LAUNCH-2.5F-B — read-only bridge: proven API price/promotion observations → protection evaluation.

This service RESOLVES the current, proven, API-sourced price evidence for one (account, store, product)
so the protection evaluation can use a proven buyer/catalog price and a proven currency instead of the
currency-unconfirmed catalog price it reads from ImportedProductRow today. It is ADVISORY only:

  * it never writes (no INSERT / UPDATE / DELETE), never calls a provider or the executor, never creates
    an ActionState, never touches the writers / retention / scheduler;
  * it returns a buyer/catalog/promotion price as EVIDENCE — a buyer price is NEVER promoted to the
    seller's revenue; seller_revenue / commission_base / subsidy stay UNKNOWN (the writers never prove
    them) and are reported as such;
  * it can never make an evaluation executable: promo_price_proven / commission_official_tariff /
    provider_capability_confirmed remain the caller's hard-False constants regardless of what is found.

Isolation is total: every query filters marketplace_account_id + marketplace_store_id + product_id +
resolution_status='resolved' + source='api'. An unassigned row (product_id NULL) is never used; a store
or account of another cabinet is impossible at the DB level (composite FKs) and is filtered here too.

Freshness is judged on last_verified_at (the latest re-confirmation), NEVER fetched_at (the first-observed
change-point). With no approved threshold the freshness is 'unknown' and nothing is called fresh.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as MPromo,
    MarketplacePromotionStoreEvidence as MPromoStore,
)

# resolver status
FOUND, MISSING, STALE, CONFLICT, UNSUPPORTED = "found", "missing", "stale", "conflict", "unsupported"
# selected price kind
CATALOG, PROMOTION = "catalog", "promotion"
# freshness
FRESH, IS_STALE, UNKNOWN = "fresh", "stale", "unknown"

_SUPPORTED = ("wb", "wildberries", "ozon", "yandex")
# The proof of a fresh promo price is a LATER slice (needs an approved freshness threshold from the real
# sync cadence). This service always reports promo_price_proven as False — it is here for symmetry, never
# set True, so the caller's hard-False constant is documented, not overridden.
PROMO_PRICE_PROVEN = False


@dataclass(frozen=True)
class ObservationResolution:
    """Typed, read-only result. `status` decides how the caller uses it; every price is EVIDENCE, never a
    proven seller revenue. `as_evidence()` is the self-contained snapshot copied into inputs_snapshot."""
    status: str
    price_kind: Optional[str] = None
    candidate_buyer_price: Optional[Decimal] = None
    catalog_price: Optional[Decimal] = None
    buyer_price: Optional[Decimal] = None
    promotion_price: Optional[Decimal] = None
    currency: Optional[str] = None
    currency_proven: bool = False
    source: str = "api"
    observation_id: Optional[str] = None
    evidence_fingerprint: Optional[str] = None
    external_product_id: Optional[str] = None
    fetched_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    freshness: str = UNKNOWN
    age_seconds: Optional[int] = None
    threshold_seconds: Optional[int] = None
    # proof statuses — surfaced verbatim; the caller must not coerce unknown into a number
    seller_revenue_status: str = "unknown"
    commission_base_status: str = "unknown"
    subsidy_status: str = "unknown"
    missing_fields: Sequence[str] = ()
    conflict_reasons: Sequence[str] = ()
    promotion_evidence: Optional[Mapping[str, object]] = None
    provenance: str = "observation_api"
    promo_price_proven: bool = PROMO_PRICE_PROVEN

    def price_usable(self) -> bool:
        """A price the caller may adopt as the advisory candidate (never as revenue). Only a non-stale,
        non-conflicting find qualifies; a stale/missing/conflict find falls back to the CSV path."""
        return self.status == FOUND and self.candidate_buyer_price is not None

    def as_evidence(self) -> dict:
        """Self-contained snapshot: no token, no credentials, no raw payload, no PII — only the resolved
        figures + provenance, so a ProtectionEvaluation stays readable after retention deletes the row."""
        return {
            "status": self.status,
            "price_kind": self.price_kind,
            "candidate_buyer_price": _s(self.candidate_buyer_price),
            "catalog_price": _s(self.catalog_price),
            "buyer_price": _s(self.buyer_price),
            "promotion_price": _s(self.promotion_price),
            "currency": self.currency,
            "currency_proven": self.currency_proven,
            "source": self.source,
            "observation_id": self.observation_id,           # soft reference — survives row deletion
            "evidence_fingerprint": self.evidence_fingerprint,
            "external_product_id": self.external_product_id,
            "fetched_at": _iso(self.fetched_at),
            "last_verified_at": _iso(self.last_verified_at),
            "freshness": self.freshness,
            "age_seconds": self.age_seconds,
            "threshold_seconds": self.threshold_seconds,
            "seller_revenue_status": self.seller_revenue_status,
            "commission_base_status": self.commission_base_status,
            "subsidy_status": self.subsidy_status,
            "missing_fields": list(self.missing_fields),
            "conflict_reasons": list(self.conflict_reasons),
            "promotion_evidence": dict(self.promotion_evidence) if self.promotion_evidence else None,
            "provenance": self.provenance,
            "promo_price_proven": self.promo_price_proven,
        }


def _s(v: Optional[Decimal]) -> Optional[str]:
    return None if v is None else str(v)


def _iso(v: Optional[datetime]) -> Optional[str]:
    return None if v is None else v.isoformat()


def _utc_naive(v: Optional[datetime]) -> Optional[datetime]:
    """Normalize to naive-UTC so an aware last_verified_at and a naive evaluated_at compare correctly."""
    if v is None:
        return None
    if v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def _order(rows):
    """Deterministic latest-of-series order: fetched_at DESC, created_at DESC, id DESC."""
    return sorted(rows, key=lambda r: (r.fetched_at, r.created_at, r.id), reverse=True)


def _window_ok(row, now: datetime) -> bool:
    """A promotion is current only inside its provider window: from <= now <= to (either bound may be
    absent). A future start or a past end excludes it."""
    if row.provider_valid_from is not None and _utc_naive(row.provider_valid_from) > now:
        return False
    if row.provider_valid_to is not None and _utc_naive(row.provider_valid_to) < now:
        return False
    return True


def _freshness(last_verified_at: Optional[datetime], now: datetime,
               threshold_seconds: Optional[int]) -> tuple[str, Optional[int]]:
    """Judged on last_verified_at only. No threshold -> 'unknown' (never 'fresh'). Missing verify ->
    fail-closed stale."""
    lv = _utc_naive(last_verified_at)
    if lv is None:
        return IS_STALE, None
    age = int((now - lv).total_seconds())
    if threshold_seconds is None:
        return UNKNOWN, age
    return (FRESH if age <= threshold_seconds else IS_STALE), age


async def resolve_current_observation(
    db: AsyncSession, *, marketplace_account_id: str, marketplace_store_id: str, product_id: str,
    marketplace: str, evaluated_at: datetime, freshness_threshold_seconds: Optional[int] = None,
) -> ObservationResolution:
    """Resolve the current proven API observation for ONE (account, store, product). READ-ONLY.

    Returns FOUND (a usable advisory price), STALE (found but past the threshold), MISSING (no api
    evidence), CONFLICT (ambiguous product/promotion — no arbitrary pick), or UNSUPPORTED. The chosen
    price is EVIDENCE only; seller revenue / commission / subsidy stay unknown; nothing is executable."""
    now = _utc_naive(evaluated_at)
    if (marketplace or "").lower() not in _SUPPORTED:
        return ObservationResolution(status=UNSUPPORTED, conflict_reasons=("marketplace_unsupported",))

    base = (
        select(MPO)
        .where(
            MPO.marketplace_account_id == marketplace_account_id,
            MPO.marketplace_store_id == marketplace_store_id,
            MPO.product_id == product_id,
            MPO.resolution_status == "resolved",
            MPO.source == "api",
        )
    )
    rows = list((await db.execute(base)).scalars().all())
    if not rows:
        return ObservationResolution(status=MISSING, conflict_reasons=("no_api_observation",))

    # Isolation guard: one product/store must map to ONE external_product_id. Several distinct current
    # external ids without a proven choice is a conflict — never pick the first.
    ext_ids = {r.external_product_id for r in rows}
    if len(ext_ids) > 1:
        return ObservationResolution(status=CONFLICT, conflict_reasons=("multiple_external_ids",))
    ext = next(iter(ext_ids))

    catalog_rows = _order([r for r in rows if r.observation_kind == "catalog"])
    promo_rows_all = [r for r in rows if r.observation_kind == "promotion"]

    # ── current active promotion (WB/Ozon live in the price table) ─────────────────────────────────
    # latest row per promotion_key, then keep only active + in-window; several DIFFERENT active promo
    # prices is a conflict (never min/max/first).
    latest_by_key: dict[str, object] = {}
    for r in _order(promo_rows_all):
        latest_by_key.setdefault(r.promotion_key, r)
    active_promos = [
        r for r in latest_by_key.values()
        if r.participation_status == "active" and _window_ok(r, now)
    ]

    def _promo_price(r):
        # Ozon proves seller_promo_price; WB proves buyer_price (planPrice). Prefer the explicit promo
        # slot, fall back to the promo buyer price. Never invent one.
        return r.seller_promo_price if r.seller_promo_price is not None else r.buyer_price

    promo_prices = {_promo_price(r) for r in active_promos if _promo_price(r) is not None}
    if len(promo_prices) > 1:
        return ObservationResolution(status=CONFLICT, external_product_id=ext,
                                     conflict_reasons=("multiple_active_promotions",))

    # ── Yandex store-attributed promotion (account-level promo table) ──────────────────────────────
    promo_note = None
    if (marketplace or "").lower() == "yandex":
        promo_note = await _yandex_promo_note(
            db, marketplace_account_id=marketplace_account_id,
            marketplace_store_id=marketplace_store_id, product_id=product_id, now=now)

    chosen = None
    kind = None
    promo_price_val = None
    if active_promos and promo_prices:
        chosen = next(iter(active_promos))
        kind = PROMOTION
        promo_price_val = _promo_price(chosen)
    elif catalog_rows:
        chosen = catalog_rows[0]
        kind = CATALOG
    else:
        # only inactive/expired promotions and no catalog — no current advisory price
        return ObservationResolution(status=MISSING, external_product_id=ext,
                                     conflict_reasons=("no_current_catalog",),
                                     promotion_evidence=promo_note)

    catalog_price = catalog_rows[0].catalog_price if catalog_rows else None
    buyer_price = catalog_rows[0].buyer_price if catalog_rows else None
    if kind == PROMOTION:
        candidate = promo_price_val
    else:
        candidate = buyer_price if buyer_price is not None else catalog_price

    currency_proven = (chosen.currency_status == "proven" and chosen.currency is not None)
    freshness, age = _freshness(chosen.last_verified_at, now, freshness_threshold_seconds)

    missing = []
    if not currency_proven:
        missing.append("currency")
    # the three economic proofs the writers never fill — always unknown, reported for honesty
    if chosen.seller_revenue_status != "provider_explicit":
        missing.append("seller_revenue")
    if chosen.commission_base_status != "provider_explicit":
        missing.append("commission_base")

    status = STALE if freshness == IS_STALE else FOUND
    return ObservationResolution(
        status=status,
        price_kind=kind,
        candidate_buyer_price=candidate,
        catalog_price=catalog_price,
        buyer_price=buyer_price,
        promotion_price=promo_price_val,
        currency=chosen.currency if currency_proven else None,
        currency_proven=currency_proven,
        source="api",
        observation_id=chosen.id,
        evidence_fingerprint=chosen.evidence_fingerprint,
        external_product_id=ext,
        fetched_at=chosen.fetched_at,
        last_verified_at=chosen.last_verified_at,
        freshness=freshness,
        age_seconds=age,
        threshold_seconds=freshness_threshold_seconds,
        seller_revenue_status=chosen.seller_revenue_status,
        commission_base_status=chosen.commission_base_status,
        subsidy_status=chosen.subsidy_status,
        missing_fields=tuple(missing),
        promotion_evidence=promo_note,
        provenance="observation_api",
        promo_price_proven=PROMO_PRICE_PROVEN,
    )


async def _yandex_promo_note(db, *, marketplace_account_id, marketplace_store_id, product_id, now):
    """Yandex promotions are an ACCOUNT-level fact. account_wide AUTO/MANUAL is NEVER attributed to a
    store — only an advisory note. A store price is used solely for exact_stores (PARTIALLY_AUTO) with a
    PROVEN, mapped campaign for THIS store. Returns an evidence note (never a candidate price here)."""
    rows = list((await db.execute(
        select(MPromo).where(
            MPromo.marketplace_account_id == marketplace_account_id,
            MPromo.product_id == product_id,
            MPromo.resolution_status == "resolved",
            MPromo.source == "api",
        ))).scalars().all())
    if not rows:
        return None
    latest = _order(rows)[0]
    store_attributed = False
    if latest.attribution_status == "exact_stores":
        ev = (await db.execute(
            select(MPromoStore).where(
                MPromoStore.promotion_observation_id == latest.id,
                MPromoStore.marketplace_store_id == marketplace_store_id,
                MPromoStore.mapping_status == "mapped",
            ))).scalars().first()
        store_attributed = ev is not None
    return {
        "provider_status": latest.provider_status,
        "participation_status": latest.participation_status,
        "attribution_status": latest.attribution_status,
        "store_attributed": store_attributed,
        "promotion_id": latest.promotion_id,
        "note": ("exact_store_evidence" if store_attributed else "account_wide_not_store_attributed"),
    }
