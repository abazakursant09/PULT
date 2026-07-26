"""
Overstock / Dead Stock Diagnosis source (Phase 7.1) — READ-ONLY, DB-headless.

Answers OVERSTOCK per (user, marketplace, sku) from the seller's OWN observed data: the current
on-hand stock (ImportedProductRow.stock, latest snapshot) and the observed sell-through velocity
(ImportedFinanceRow.quantity aggregated over the observed window). The MIRROR of Supply
Diagnosis:

  * DEAD STOCK  — stock present but ZERO observed sales across a sufficient window → capital
    frozen with no movement.
  * EXCESS STOCK — stock present, some sales, but days-of-cover (stock ÷ avg daily sell-through)
    is TOO HIGH → far more inventory than recent demand warrants.

Diagnoses the observed PRESENT overstock — days_of_cover is a current-rate ratio, NOT a forecast
of the future. No absolute benchmark, no competitor compare, no discount/liquidation action.

Independent contour. It is the mirror of, and does NOT reuse, supply_signal; reimplemented
independently — does not import from services/supply / rating / review_velocity / money_leak /
revenue.

Honest absence (emit NOTHING): no stock snapshot / stock None / stock <= 0; fewer than
MIN_OBSERVED_DAYS distinct observed finance days (cannot establish a rate); or — for excess —
days_of_cover at/below the not-overstocked horizon. Evidence is a DISCIPLINE (stock, velocity,
days_of_cover, day count travel in the signal text + evidence dict) — not a new component.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow

# ── deterministic constants (data-sufficiency gates + cover bands) ────────────
MIN_OBSERVED_DAYS = 3      # need ≥3 distinct observed finance days to judge a rate
LOW_COVER = 90.0          # days_of_cover ≥ 90  (~3 months)  → low
MED_COVER = 120.0         # ≥ 120 (~4 months) → medium
HIGH_COVER = 180.0        # ≥ 180 (~6 months) → high ; < 90 → not overstocked (emit nothing)


@dataclass(frozen=True)
class OverstockDiagnosis:
    problem_type: str          # dead_stock | excess_stock
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    stock: int
    velocity: float            # observed avg daily units sold (0.0 for dead stock)
    distinct_days: int
    days_of_cover: Optional[float]   # stock ÷ velocity (None when velocity == 0, dead stock)

    @property
    def signal_key(self) -> str:
        return f"overstock_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"overstock_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "stock": self.stock,
            "velocity": round(self.velocity, 3),
            "days_of_cover": round(self.days_of_cover, 1) if self.days_of_cover is not None else None,
            "distinct_days": self.distinct_days,
        }


async def latest_stock(db: AsyncSession, user_id: str, marketplace: str, sku: str,
                       *, source: str = "csv", store_id: str | None = None) -> Optional[int]:
    """Most recent observed on-hand stock for one (user, marketplace, sku). None if no row.

    Source-single (PULT-LAUNCH-1.4.5H): reads ONE `source` (default 'csv'); `store_id` scopes to one
    store so two stores never mix."""
    conds = [
        ImportedProductRow.user_id == user_id,
        ImportedProductRow.marketplace == marketplace,
        ImportedProductRow.sku == sku,
        ImportedProductRow.source == source,
    ]
    if store_id is not None:
        conds.append(ImportedProductRow.marketplace_store_id == store_id)
    return (await db.execute(
        select(ImportedProductRow.stock).where(*conds)
        .order_by(ImportedProductRow.created_at.desc()).limit(1))).scalar()


async def observed_velocity(
    db: AsyncSession, user_id: str, marketplace: str, sku: str) -> Tuple[int, int]:
    """(distinct_observed_days, total_units) for one (user, marketplace, sku) from finance.
    DB-headless — ImportedFinanceRow.quantity only, keyed by observed date."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.quantity).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    per_day: dict = {}
    for date, qty in rows:
        per_day[date] = per_day.get(date, 0) + int(qty or 0)
    return len(per_day), sum(per_day.values())


def classify_overstock(
    stock: Optional[int], distinct_days: int, total_units: int, *, marketplace: str, sku: str
) -> Optional[OverstockDiagnosis]:
    """Observed overstock (dead stock or excess cover), or None (honest absence)."""
    if stock is None or stock <= 0:
        return None                                    # no / depleted stock — nothing frozen
    if distinct_days < MIN_OBSERVED_DAYS:
        return None                                    # insufficient observed window for a rate

    velocity = total_units / distinct_days

    # DEAD STOCK — stock present, zero observed sales across a sufficient window.
    if total_units == 0:
        return OverstockDiagnosis(
            problem_type="dead_stock", marketplace=marketplace, sku=sku,
            priority_level="high", effect_band="high", stock=stock,
            velocity=0.0, distinct_days=distinct_days, days_of_cover=None)

    # EXCESS STOCK — days-of-cover too high relative to observed demand.
    days = stock / velocity
    if days >= HIGH_COVER:
        priority = band = "high"
    elif days >= MED_COVER:
        priority = band = "medium"
    elif days >= LOW_COVER:
        priority = band = "low"
    else:
        return None                                    # < 90 days cover → not overstocked

    return OverstockDiagnosis(
        problem_type="excess_stock", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, stock=stock,
        velocity=velocity, distinct_days=distinct_days, days_of_cover=days)
