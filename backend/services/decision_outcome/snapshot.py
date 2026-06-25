"""
Canonical Engine Signal Snapshot (Decision Outcome A3) — normalize every engine
lifecycle signal into one uniform, promotion-ready shape.

Read-only. Reads the five engine signal tables (seo / advertising / review /
growth / legal) and emits normalized EngineSignalSnapshot items so the future
bridge (A6) can promote signal → decision without knowing per-contour quirks.

Key normalization (the whole point of A3):
  * 3-part engine keys (seo_/adv_/growth_/legal_ : <mp> : <sku>) → canonical = raw.
  * Review's 4-part key (rev_<t>:<mp>:<sku>:<review_id>) → canonical drops the 4th
    segment; review_id is preserved in source_context (never lost).
  * action_key / metric_key come from CANONICAL_INSIGHT_TYPES (the registry is the
    contract), keyed by the signal's signal_key.

Honest degradation: a signal whose signal_key is unknown to the registry, or whose
raw key has the wrong arity, is returned as an InvalidSignalItem with a reason —
the snapshot never raises and never drops it silently.

NO DB writes, NO decision, NO link, NO measurement, NO change to engine signals.
Pure read + transform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.pricing_signal import PricingSignal

from .registry import BY_SIGNAL_KEY

# contour → (model, signal_table name)
_CONTOUR_MODELS = (
    ("seo", SeoSignal, "seo_signal"),
    ("advertising", AdvertisingSignal, "advertising_signal"),
    ("review", ReviewSignal, "review_signal"),
    ("growth", GrowthSignal, "growth_signal"),
    ("legal", LegalSignal, "legal_signal"),
    ("pricing", PricingSignal, "pricing_signal"),
)


@dataclass(frozen=True)
class EngineSignalSnapshot:
    contour: str
    signal_table: str
    signal_id: str
    raw_insight_key: Optional[str]
    canonical_insight_key: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]
    action_key: Optional[str]          # from registry (None until A6 capability gating)
    metric_key: Optional[str]          # from registry
    status: Optional[str]
    evidence_hash: Optional[str]
    created_at: Optional[datetime]
    source_context: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class InvalidSignalItem:
    contour: str
    signal_table: str
    signal_id: str
    raw_insight_key: Optional[str]
    reason: str
    source_context: Mapping[str, object] = field(default_factory=dict)


SnapshotItem = Union[EngineSignalSnapshot, InvalidSignalItem]


def _source_context(contour: str, sig) -> dict:
    """Per-contour provenance preserved alongside the canonical key."""
    ctx = {
        "signal_key": sig.signal_key,
        "recommended_action_key": getattr(sig, "recommended_action_key", None),
        "listing_id": getattr(sig, "listing_id", None),
        "problem_type": getattr(sig, "problem_type", None) or getattr(sig, "requirement_type", None),
    }
    if contour == "review":
        ctx["review_id"] = getattr(sig, "review_id", None)
    if contour == "legal":
        ctx["subject_type"] = getattr(sig, "subject_type", None)
        ctx["subject_ref"] = getattr(sig, "subject_ref", None)
    return {k: v for k, v in ctx.items() if v is not None}


def _normalize(contour: str, table: str, sig) -> SnapshotItem:
    raw = sig.insight_key
    ctx = _source_context(contour, sig)
    entry = BY_SIGNAL_KEY.get(sig.signal_key)

    if entry is None:
        return InvalidSignalItem(contour, table, sig.id, raw,
                                 reason=f"unknown_signal_type: {sig.signal_key}", source_context=ctx)
    if not raw:
        return InvalidSignalItem(contour, table, sig.id, raw,
                                 reason="missing_insight_key", source_context=ctx)

    parts = raw.split(":")
    if entry.carries_review_id:
        # review: expect 4 parts → canonical is first 3; keep review_id
        if len(parts) != 4:
            return InvalidSignalItem(contour, table, sig.id, raw,
                                     reason=f"unexpected_key_arity: {len(parts)} (expected 4)",
                                     source_context=ctx)
        canonical = ":".join(parts[:3])
        if "review_id" not in ctx:
            ctx["review_id"] = parts[3]
    else:
        if len(parts) != 3:
            return InvalidSignalItem(contour, table, sig.id, raw,
                                     reason=f"unexpected_key_arity: {len(parts)} (expected 3)",
                                     source_context=ctx)
        canonical = raw

    return EngineSignalSnapshot(
        contour=contour, signal_table=table, signal_id=sig.id,
        raw_insight_key=raw, canonical_insight_key=canonical,
        marketplace=sig.marketplace, sku=sig.sku,
        action_key=entry.action_key, metric_key=entry.default_metric_key,
        status=sig.status, evidence_hash=sig.evidence_hash, created_at=sig.created_at,
        source_context=ctx,
    )


async def build_signal_snapshot(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    status: Optional[str] = None, listing_id: Optional[str] = None,
) -> List[SnapshotItem]:
    """Normalized snapshot of engine signals for a seller. Read-only.

    Filters: contour (single contour), status, listing_id — all optional.
    Returns EngineSignalSnapshot for normalizable signals, InvalidSignalItem
    (with reason) for the rest. Never writes, never raises on a bad signal."""
    out: List[SnapshotItem] = []
    for name, model, table in _CONTOUR_MODELS:
        if contour and contour != name:
            continue
        stmt = select(model).where(model.user_id == user_id)
        if status:
            stmt = stmt.where(model.status == status)
        if listing_id:
            stmt = stmt.where(model.listing_id == listing_id)
        rows = (await db.execute(stmt)).scalars().all()
        for sig in rows:
            out.append(_normalize(name, table, sig))
    return out
