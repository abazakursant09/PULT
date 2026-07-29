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
from datetime import datetime, timedelta, timezone
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


# ── UTC normalization (one contract, used by both the fingerprint and the 24h bump) ─────────────────
def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Canonical naive-UTC form of a datetime. An AWARE value is converted to UTC (astimezone), never
    merely stripped of its offset; a NAIVE value is treated as already-UTC per the project contract
    (every writer stamps datetime.utcnow()). Returns a naive UTC datetime so aware/naive values are
    directly comparable and hash-identical (12:00+03:00 and 09:00Z collapse to the same instant)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── canonical fingerprint ───────────────────────────────────────────────────────────────────────────
def _canon(value):
    """Deterministic JSON-ready form: Decimal→fixed-scale string, datetime→UTC ISO(sec), lists SORTED,
    NULL preserved (never coerced to 0/''). bool is kept as bool (checked before int)."""
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return format(value, ".2f")                       # column scale Numeric(18, 2): 100 → "100.00"
    if isinstance(value, datetime):
        return _to_utc(value).replace(microsecond=0).isoformat()   # UTC-normalized; NULL stays None
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


def price_fingerprint(*, resolution_status: str, product_id: Optional[str], observation_kind: str,
                      promotion_key: str, fields: dict) -> str:
    # product_id (the local product this observation is bound to) IS semantic: a re-mapping
    # NULL→A / A→B is a real change of the evidence and MUST create a new version, so the internal
    # UUID goes into the fingerprint (never emitted anywhere but the hash).
    payload = {"resolution_status": resolution_status, "product_id": product_id,
               "observation_kind": observation_kind, "promotion_key": promotion_key}
    for key in _PRICE_SEMANTIC:
        payload.setdefault(key, fields.get(key))
    return evidence_fingerprint(payload)


def promo_fingerprint(*, resolution_status: str, product_id: Optional[str], fields: dict,
                      child_set: Sequence[Sequence]) -> str:
    payload = {"resolution_status": resolution_status, "product_id": product_id,
               "children": [list(c) for c in child_set]}
    for key in _PROMO_SEMANTIC:
        payload[key] = fields.get(key)
    return evidence_fingerprint(payload)


# ── last_verified bump (shared) ─────────────────────────────────────────────────────────────────────
def _maybe_bump(row, now: datetime) -> None:
    """Refresh last_verified_at ONLY when it is already ≥ 24h stale. Both sides are normalized to UTC
    first (via _to_utc), so a tz-aware value read back from PostgreSQL and the naive `now` are compared
    on ONE UTC scale and the 24h boundary is exact regardless of the stored offset."""
    lv = _to_utc(row.last_verified_at)
    now_utc = _to_utc(now)
    if lv is None or now_utc - lv >= _VERIFY_BUMP:
        row.last_verified_at = now


# ── price / promotion-price observation (Ozon+WB catalog & promotion, Yandex catalog) ────────────────
async def observe_price(db, *, run_id: str, account_id: str, store_id: str, ext: str,
                        observation_kind: str, promotion_key: str, product_id: Optional[str],
                        source: str, now: datetime, fields: dict) -> None:
    """Change-only write into MarketplacePriceObservation. Compares against the immediate latest of the
    series (store, external product, KIND, promotion, source); inserts a new append-only version only
    when the evidence changed (or the latest carries no fingerprint), else bumps last_verified_at."""
    resolution_status = "resolved" if product_id else "unassigned"
    fp = price_fingerprint(resolution_status=resolution_status, product_id=product_id,
                           observation_kind=observation_kind, promotion_key=promotion_key, fields=fields)

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
    # Canonical child set: sorted [external_store_id, mapping_status, marketplace_store_id]. The bound
    # store IS semantic — unmapped vs mapped→A vs mapped→B are DIFFERENT evidence and must version — so
    # the internal store UUID goes INTO the fingerprint (hash only; never emitted to a log or the API).
    child_set = [[ext_store, ("mapped" if store_id else "unmapped"), store_id]
                 for (ext_store, store_id) in child_evidence]
    fp = promo_fingerprint(resolution_status=resolution_status, product_id=product_id,
                           fields=fields, child_set=child_set)

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
