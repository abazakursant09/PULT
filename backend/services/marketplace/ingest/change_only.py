"""PULT-LAUNCH-2.5E-1 — change-only observation writers + evidence fingerprint (feature OFF).

Stops the unbounded growth of IDENTICAL price/promotion observations. Every ingest pass used to
append one row per series per run (96 runs/day → 96 identical rows/day per product). Here the writer
compares the NEW evidence against the immediate LATEST version of the same logical series:

  * if the evidence is semantically identical (same `evidence_fingerprint`) — NO new row is created;
    the latest row's `last_verified_at` is bumped, and ONLY when it is already ≥ 24h stale (so a
    15-minute cadence does not rewrite the row every tick);
  * if any BUSINESS field changed (or the latest has no fingerprint yet) — a NEW append-only version
    is inserted, `fetched_at = last_verified_at = now`, carrying the new fingerprint.

The fingerprint is SHA-256 of a canonical JSON of the SEMANTIC fields only (never row id, run id,
fetched_at/created_at, tokens, or the internal product/store UUIDs). `fingerprint_version` is baked
into the payload so a future field-set change is a clean, versioned break rather than a silent
mismatch. Decimals serialize at the column scale ("100.00"); NULL is distinct from 0; every list
(missing_fields, the Yandex child campaign set) is sorted so element order never changes the hash.

Latest is chosen deterministically by (fetched_at DESC, created_at DESC, id DESC) — NEVER by
last_verified_at, so a 100→90→100 history keeps all three change-points (the third 100 is compared
only against the immediate 90, never the historical first 100).

Reached only through run_api_sync_once; while api_data_sync_enabled is False (the default) this code
makes ZERO provider calls and writes nothing. No scheduler tick invokes it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import select

from models.marketplace_price_observation import MarketplacePriceObservation
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation, MarketplacePromotionStoreEvidence)

# The fingerprint algorithm version. Baked into every canonical payload so that changing the semantic
# field set (below) is an explicit, versioned break: every series gets ONE forced new version on the
# next pass instead of a silent hash collision/mismatch. Bump ONLY when the field set changes.
FINGERPRINT_VERSION = 1

# A repeated, unchanged observation refreshes last_verified_at ONLY when the latest row is already at
# least this stale, so a 15-minute sync cadence does not rewrite the same row 96×/day.
_VERIFY_BUMP = timedelta(hours=24)

# ── SEMANTIC field sets (corrected Design 2.5E §3) ──────────────────────────────────────────────────
# Everything that makes an observation DIFFERENT evidence. Technical provenance (id, ingest_run_id,
# fetched_at, created_at, external_row_id, source, provider_dataset) and the series-identity columns
# that are constant within a series are deliberately EXCLUDED. product_id (an internal UUID) is
# excluded — the FACT of resolution is carried by resolution_status, which IS in the fingerprint.
_PRICE_SEMANTIC = (
    "observation_kind", "promotion_id", "promotion_key", "promotion_type", "participation_status",
    "catalog_price", "buyer_price", "seller_promo_price", "marketplace_subsidy",
    "expected_seller_revenue", "commission_base", "provider_min_price", "auto_action_enabled",
    "club_buyer_price", "currency", "currency_status", "seller_revenue_status",
    "commission_base_status", "subsidy_status", "provider_valid_from", "provider_valid_to",
    "missing_fields",
)
_PROMO_SEMANTIC = (
    "provider_status", "participation_status", "auto_participation", "attribution_status",
    "pre_promo_price", "promo_buyer_price", "promo_max_price", "currency", "currency_status",
    "promotion_start_at", "promotion_end_at", "missing_fields",
)


# ── canonical fingerprint ───────────────────────────────────────────────────────────────────────────
def _canon(value):
    """Deterministic JSON-ready form: Decimal→fixed-scale string, datetime→ISO(sec), lists SORTED,
    NULL preserved (never coerced to 0/''). bool is kept as bool (checked before int)."""
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return format(value, ".2f")                       # column scale Numeric(18, 2): 100 → "100.00"
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()   # provider-declared window; NULL stays None
    if isinstance(value, (list, tuple)):
        items = [_canon(v) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    if isinstance(value, dict):
        return {k: _canon(value[k]) for k in value}
    return str(value)


def evidence_fingerprint(payload: dict) -> str:
    """SHA-256 hex of the canonical JSON of `payload`, namespaced by FINGERPRINT_VERSION. NEVER uses
    Python hash() (unstable across processes)."""
    canonical = {"fingerprint_version": FINGERPRINT_VERSION, "fields": _canon(payload)}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def price_fingerprint(*, resolution_status: str, observation_kind: str, promotion_key: str,
                      fields: dict) -> str:
    payload = {"resolution_status": resolution_status, "observation_kind": observation_kind,
               "promotion_key": promotion_key}
    for key in _PRICE_SEMANTIC:
        payload.setdefault(key, fields.get(key))
    return evidence_fingerprint(payload)


def promo_fingerprint(*, resolution_status: str, fields: dict,
                      child_set: Sequence[Sequence[str]]) -> str:
    payload = {"resolution_status": resolution_status, "children": [list(c) for c in child_set]}
    for key in _PROMO_SEMANTIC:
        payload[key] = fields.get(key)
    return evidence_fingerprint(payload)


# ── last_verified bump (shared) ─────────────────────────────────────────────────────────────────────
def _maybe_bump(row, now: datetime) -> None:
    """Refresh last_verified_at ONLY when it is already ≥ 24h stale. Coerce a tz-aware value (Postgres
    DateTime(timezone=True)) to naive-UTC before subtracting the naive `now`, so the comparison never
    raises across SQLite (naive) and PostgreSQL (aware)."""
    lv = row.last_verified_at
    if lv is None:
        row.last_verified_at = now
        return
    if lv.tzinfo is not None:
        lv = lv.replace(tzinfo=None)
    if now - lv >= _VERIFY_BUMP:
        row.last_verified_at = now


# ── price / promotion-price observation (Ozon+WB catalog & promotion, Yandex catalog) ────────────────
async def observe_price(db, *, run_id: str, account_id: str, store_id: str, ext: str,
                        observation_kind: str, promotion_key: str, product_id: Optional[str],
                        source: str, now: datetime, fields: dict) -> None:
    """Change-only write into MarketplacePriceObservation. Compares against the immediate latest of the
    series (store, external product, KIND, promotion, source); inserts a new append-only version only
    when the evidence changed (or the latest carries no fingerprint), else bumps last_verified_at."""
    resolution_status = "resolved" if product_id else "unassigned"
    fp = price_fingerprint(resolution_status=resolution_status, observation_kind=observation_kind,
                           promotion_key=promotion_key, fields=fields)

    latest = (await db.execute(select(MarketplacePriceObservation).where(
        MarketplacePriceObservation.marketplace_store_id == store_id,
        MarketplacePriceObservation.external_product_id == ext,
        MarketplacePriceObservation.observation_kind == observation_kind,
        MarketplacePriceObservation.promotion_key == promotion_key,
        MarketplacePriceObservation.source == source,
    ).order_by(
        MarketplacePriceObservation.fetched_at.desc(),
        MarketplacePriceObservation.created_at.desc(),
        MarketplacePriceObservation.id.desc(),
    ).limit(1))).scalars().first()

    if latest is not None and latest.evidence_fingerprint is not None \
            and latest.evidence_fingerprint == fp:
        _maybe_bump(latest, now)
        return

    row = MarketplacePriceObservation(
        id=str(uuid.uuid4()), ingest_run_id=run_id, marketplace_account_id=account_id,
        marketplace_store_id=store_id, external_product_id=ext, observation_kind=observation_kind,
        promotion_key=promotion_key, source=source, product_id=product_id,
        resolution_status=resolution_status, fetched_at=now, last_verified_at=now,
        evidence_fingerprint=fp)
    db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)


# ── Yandex account-level promotion observation (parent + PARTIALLY_AUTO children) ─────────────────────
async def observe_promotion(db, *, run_id: str, account_id: str, offer_id: str, promo_id: str,
                            product_id: Optional[str], now: datetime, fields: dict,
                            child_evidence: Sequence[tuple]) -> None:
    """Change-only write of a Yandex promotion PARENT + its PARTIALLY_AUTO campaign CHILDREN. The child
    set is part of the parent's semantic identity, so `child_evidence` (a sequence of
    (external_store_id, store_id) — store_id None when unmapped) is folded into the fingerprint. When
    the evidence is unchanged: no new parent, children NOT recreated, only a possible last_verified
    bump. When it changed: a NEW parent + ALL its children are inserted atomically in this transaction."""
    resolution_status = "resolved" if product_id else "unassigned"
    # Canonical child set: sorted [external_store_id, mapping_status] (marketplace_store_id — an internal
    # UUID — is NEVER in the fingerprint; mapping_status carries mapped↔unmapped).
    child_set = [[ext_store, ("mapped" if store_id else "unmapped")]
                 for (ext_store, store_id) in child_evidence]
    fp = promo_fingerprint(resolution_status=resolution_status, fields=fields, child_set=child_set)

    latest = (await db.execute(select(MarketplacePromotionObservation).where(
        MarketplacePromotionObservation.marketplace_account_id == account_id,
        MarketplacePromotionObservation.external_product_id == offer_id,
        MarketplacePromotionObservation.promotion_id == promo_id,
        MarketplacePromotionObservation.source == "api",
    ).order_by(
        MarketplacePromotionObservation.fetched_at.desc(),
        MarketplacePromotionObservation.created_at.desc(),
        MarketplacePromotionObservation.id.desc(),
    ).limit(1))).scalars().first()

    if latest is not None and latest.evidence_fingerprint is not None \
            and latest.evidence_fingerprint == fp:
        _maybe_bump(latest, now)
        return

    parent = MarketplacePromotionObservation(
        id=str(uuid.uuid4()), ingest_run_id=run_id, marketplace_account_id=account_id,
        marketplace="yandex", external_product_id=offer_id, promotion_id=promo_id,
        promotion_type="yandex_promo", source="api", provider_dataset="promos",
        product_id=product_id, resolution_status=resolution_status, fetched_at=now,
        last_verified_at=now, evidence_fingerprint=fp)
    db.add(parent)
    for key, value in fields.items():
        setattr(parent, key, value)
    await db.flush()   # materialize parent.id for the children

    for ext_store, store_id in child_evidence:
        db.add(MarketplacePromotionStoreEvidence(
            id=str(uuid.uuid4()), promotion_observation_id=parent.id, marketplace_account_id=account_id,
            external_store_id=ext_store, marketplace_store_id=store_id,
            mapping_status="mapped" if store_id else "unmapped", created_at=now))
