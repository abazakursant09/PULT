"""
Supply / Replenishment Diagnosis source (Phase 4.1) — READ-ONLY, DB-headless.

Answers stock-out risk per (user, marketplace, sku) from the seller's OWN observed data:
the current on-hand stock (ImportedProductRow.stock, latest snapshot) and the observed
daily sell-through velocity (ImportedFinanceRow.quantity aggregated over the observed
window). Diagnoses the observed PRESENT runway — days_to_oos is a current-rate ratio
(stock ÷ avg daily units sold), NOT a forecast of the future.

Independent contour. It complements, does NOT replace, the operations low_stock signal
(a single binary threshold) and does NOT reuse operations_signal. Pure diagnosis — no
treatment, no executor.

Honest absence (emit NOTHING): no stock snapshot / stock None / stock <= 0; fewer than
MIN_VELOCITY_DAYS distinct sales days; zero velocity (no observed sales → cannot compute a
runway, never fabricated); or a runway at/above the not-at-risk horizon. Evidence is a
DISCIPLINE (stock, velocity, days_to_oos, day count travel in the signal text + evidence
dict) — not a new component.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow

# ── deterministic constants (no fabricated numbers, no external tuning) ───────
MIN_VELOCITY_DAYS = 3     # need ≥3 distinct sales days to average a stable daily rate
CRIT_DAYS = 7.0          # runway < 7 days  → critical
HIGH_DAYS = 14.0         # runway < 14 days → high
MED_DAYS = 30.0          # runway < 30 days → medium ; ≥30 → not at risk (emit nothing)


@dataclass(frozen=True)
class SupplyDiagnosis:
    problem_type: str          # supply_stockout_risk
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # critical | high | medium
    effect_band: str           # high | medium
    days_to_oos: float         # observed runway (stock ÷ avg daily sell-through)
    stock: int
    velocity: float            # observed avg daily units sold
    distinct_days: int

    @property
    def signal_key(self) -> str:
        return "supply_stockout_risk"

    @property
    def insight_key(self) -> str:
        return f"supply_stockout_risk:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "stock": self.stock,
            "velocity": round(self.velocity, 3),
            "days_to_oos": round(self.days_to_oos, 1),
            "distinct_days": self.distinct_days,
        }


async def latest_stock(db: AsyncSession, user_id: str, marketplace: str, sku: str) -> Optional[int]:
    """Most recent observed on-hand stock for one (user, marketplace, sku). None if no row."""
    return (await db.execute(
        select(ImportedProductRow.stock).where(
            ImportedProductRow.user_id == user_id,
            ImportedProductRow.marketplace == marketplace,
            ImportedProductRow.sku == sku)
        .order_by(ImportedProductRow.created_at.desc()).limit(1))).scalar()


async def observed_velocity(
    db: AsyncSession, user_id: str, marketplace: str, sku: str) -> Tuple[int, int]:
    """(distinct_sales_days, total_units) for one (user, marketplace, sku) from finance.
    DB-headless — ImportedFinanceRow.quantity only."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.quantity).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    per_day: dict = {}
    for date, qty in rows:
        per_day[date] = per_day.get(date, 0) + (qty or 0)
    return len(per_day), sum(per_day.values())


def classify_supply_risk(
    stock: Optional[int], distinct_days: int, total_units: int, *, marketplace: str, sku: str
) -> Optional[SupplyDiagnosis]:
    """Observed stock-out runway risk, or None (honest absence)."""
    if stock is None or stock <= 0:
        return None                                    # no / depleted stock snapshot
    if distinct_days < MIN_VELOCITY_DAYS:
        return None                                    # insufficient sales history for a rate
    velocity = total_units / distinct_days
    if velocity <= 0:
        return None                                    # no observed sales → no runway (never fabricated)

    days = stock / velocity
    if days < CRIT_DAYS:
        priority, band = "critical", "high"
    elif days < HIGH_DAYS:
        priority, band = "high", "high"
    elif days < MED_DAYS:
        priority, band = "medium", "medium"
    else:
        return None                                    # ≥30 days runway → not at risk

    return SupplyDiagnosis(
        problem_type="supply_stockout_risk", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, days_to_oos=days, stock=stock,
        velocity=velocity, distinct_days=distinct_days)
