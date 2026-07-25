"""
Price Erosion / Discount Creep Diagnosis source (Phase 8.1) — READ-ONLY, DB-headless.

Answers PRICE EROSION per (user, marketplace, sku) from the seller's OWN observed
ImportedProductRow.price across DATED import snapshots (imports are insert-only, so each import
adds a new dated row → a real price time-series). Diagnoses the observed PRESENT decline
(baseline snapshot → latest snapshot), NOT a forecast.

SELF-REFERENTIAL: compares this product's OWN baseline price to its latest confirmed price —
the relative drop is measured against the product's own earlier price, NEVER an absolute price
floor, category benchmark, or competitor price. Reimplemented independently — does not import
from services/pricing / rating / overstock / money_leak / revenue.

Confirmed decline only: requires MIN_SNAPSHOTS distinct dated price snapshots; the latest price
must be below the baseline (oldest) by at least the smallest relative band; and the
second-newest snapshot must also be below baseline (so a single-snapshot dip on an otherwise
flat history is rejected — noise, not a confirmed drift).

Honest absence (emit NOTHING): < MIN_SNAPSHOTS price snapshots; non-positive baseline price;
latest snapshot missing price; flat or rising price; relative drop below the smallest band;
single-snapshot dip not confirmed by the prior snapshot. No price-write action, ever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_product import ImportedProductRow

# ── deterministic constants (self-referential relative bands, no benchmark) ───
MIN_SNAPSHOTS = 3
LOW_DROP = 0.05          # relative drop >= 5%  and < 15% → low
MED_DROP = 0.15          # >= 15% and < 30% → medium
SEVERE_DROP = 0.30       # >= 30% → high


@dataclass(frozen=True)
class PriceErosionDiagnosis:
    problem_type: str          # discount_creep
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    relative_drop: float       # (baseline − latest) / baseline (positive = erosion)
    latest_price: float
    baseline_price: float
    prev_price: float
    snapshot_count: int

    @property
    def signal_key(self) -> str:
        return f"price_erosion_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"price_erosion_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "baseline_price": round(self.baseline_price, 2),
            "latest_price": round(self.latest_price, 2),
            "prev_price": round(self.prev_price, 2),
            "relative_drop": round(self.relative_drop, 4),
            "snapshot_count": self.snapshot_count,
        }


async def build_price_series(
    db: AsyncSession, user_id: str, marketplace: str, sku: str,
    *, source: str = "csv", store_id: str | None = None,
) -> List[float]:
    """Observed price per dated ImportedProductRow snapshot, oldest→newest, for one
    (user, marketplace, sku). DB-headless — ImportedProductRow only, ordered by created_at
    (snapshot date), NOT insert order. Only price-bearing snapshots (price not None).

    Source-single (PULT-LAUNCH-1.4.5H): a series is built from ONE `source` only — never interleaving
    CSV and API snapshots, which would fabricate a price trend. `source` defaults to 'csv' (today's
    behaviour); the resolver passes 'api' + a `store_id` when policy selects the API for that store.
    `store_id` scopes to one campaign store so two Yandex stores never mix."""
    conds = [
        ImportedProductRow.user_id == user_id,
        ImportedProductRow.marketplace == marketplace,
        ImportedProductRow.sku == sku,
        ImportedProductRow.price.isnot(None),
        ImportedProductRow.source == source,
    ]
    if store_id is not None:
        conds.append(ImportedProductRow.marketplace_store_id == store_id)
    rows = (await db.execute(
        select(ImportedProductRow.price).where(*conds)
        .order_by(ImportedProductRow.created_at.asc()))).all()
    return [float(p) for (p,) in rows]


def classify_price_erosion(
    series: List[float], *, marketplace: str, sku: str
) -> Optional[PriceErosionDiagnosis]:
    """Confirmed self-referential downward price drift, or None (honest absence). Observed-only."""
    if len(series) < MIN_SNAPSHOTS:
        return None                                          # thin price history

    baseline = series[0]
    latest = series[-1]
    prev = series[-2]                                        # second-newest
    if baseline <= 0:
        return None                                          # non-positive baseline — cannot ratio

    relative_drop = (baseline - latest) / baseline
    if relative_drop < LOW_DROP:
        return None                                          # flat / rising / below smallest band
    if prev >= baseline:
        return None                                          # single-snapshot dip — noise, not confirmed

    if relative_drop >= SEVERE_DROP:
        priority = band = "high"
    elif relative_drop >= MED_DROP:
        priority = band = "medium"
    else:
        priority = band = "low"

    return PriceErosionDiagnosis(
        problem_type="discount_creep", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, relative_drop=relative_drop,
        latest_price=latest, baseline_price=baseline, prev_price=prev,
        snapshot_count=len(series))
