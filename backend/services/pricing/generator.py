"""
Pricing Signal Generator (A3-pre) — the single entry point.

Pipeline (observed-only, marketplace-isolated, flush-only):
  build_pricing_snapshot()  → None when no finance (→ no signal)
   → evaluate the RULE_REGISTRY (pure)
   → reconcile_signals()     → one live PricingSignal per insight_key

No audit/problem tables (A3-pre keeps the footprint to the single pricing_signal
table). No Decision bridge, no binding, no executor, no payload, no AI, no forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .snapshot import build_pricing_snapshot
from .rules import RULE_REGISTRY, PricingThresholds
from .reconciliation import reconcile_signals, ReconcileResult


@dataclass
class PricingGenerateResult:
    evaluated: bool                       # False when no finance snapshot existed
    reconciliation: Optional[ReconcileResult] = None


async def generate_pricing_signals(
    db: AsyncSession, *, user_id: str, marketplace: str, sku: Optional[str],
    thresholds: PricingThresholds, listing_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> PricingGenerateResult:
    """Generate/reconcile observed pricing/margin signals for one (user, marketplace,
    sku). Returns evaluated=False (no signals touched) when no finance exists."""
    ts = now or datetime.utcnow()
    snap = await build_pricing_snapshot(db, user_id=user_id, marketplace=marketplace,
                                        sku=sku, listing_id=listing_id, now=ts)
    if snap is None:
        return PricingGenerateResult(evaluated=False)

    evaluations = [r.evaluate(snap, thresholds) for r in RULE_REGISTRY]
    recon = await reconcile_signals(
        db, user_id=user_id, listing_id=listing_id, marketplace=marketplace, sku=sku,
        evaluations=evaluations, now=ts)
    return PricingGenerateResult(evaluated=True, reconciliation=recon)
