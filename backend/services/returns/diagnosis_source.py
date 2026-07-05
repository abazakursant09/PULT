"""
Returns Diagnosis source (Phase R1b) — READ-ONLY, DB-headless.

Answers a rising RETURN RATE per (user, marketplace, sku) from the seller's OWN observed data:
the returns quantity (ImportedReturnRow.returns_qty) and the units sold (ImportedFinanceRow.
quantity), both keyed by observed date. Diagnoses the observed PRESENT rise (earlier window vs
recent window), NOT a forecast.

SELF-REFERENTIAL: the observed selling period is split into an earlier window H1 and a recent
window H2; each window's return rate = returns_qty / units_sold is computed, and a rise is a
CONFIRMED relative increase of the recent rate vs the product's OWN earlier rate. NEVER an
absolute floor, category benchmark, or competitor compare. Reimplemented independently — does not
import from any other contour.

DOUBLE-COUNT DISCIPLINE: built from return FREQUENCY only (returns_qty vs units sold). This module
NEVER reads net_profit and NEVER uses return_amount as a profit loss — net_profit may already
reflect returns, so a money-loss figure would double-count. return_amount is deliberately not
consumed here.

Honest absence (emit NOTHING): no returns data; no sales data; fewer than MIN_OBSERVED_DAYS
distinct observed sale days; either window below MIN_WINDOW_UNITS sold (insufficient volume);
earlier_rate == 0 (cannot compare a rise safely); recent_rate <= earlier_rate (flat / falling);
relative_rise below the smallest band.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow

# ── deterministic constants (data-sufficiency gates + self-referential bands) ──
MIN_OBSERVED_DAYS = 3      # need ≥3 distinct observed sale days to form two windows
MIN_WINDOW_UNITS = 5       # each window needs ≥5 units sold for a stable rate (noise gate)
LOW_RISE = 0.25           # recent rate up ≥25% vs earlier rate → low
MED_RISE = 0.50           # ≥50% → medium
SEVERE_RISE = 1.00        # ≥100% (doubled) → high


@dataclass(frozen=True)
class ReturnsDiagnosis:
    problem_type: str          # return_rate_rise
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    relative_rise: float       # (recent_rate − earlier_rate) / earlier_rate
    earlier_return_rate: float
    recent_return_rate: float
    earlier_returns: int
    recent_returns: int
    earlier_units: int
    recent_units: int
    earlier_window_start: str
    earlier_window_end: str
    recent_window_start: str
    recent_window_end: str
    distinct_days: int

    @property
    def signal_key(self) -> str:
        return f"returns_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"returns_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        # return FREQUENCY only — NO net_profit, NO return_amount (double-count discipline)
        return {
            "earlier_returns": self.earlier_returns,
            "recent_returns": self.recent_returns,
            "earlier_units": self.earlier_units,
            "recent_units": self.recent_units,
            "earlier_return_rate": round(self.earlier_return_rate, 4),
            "recent_return_rate": round(self.recent_return_rate, 4),
            "relative_rise": round(self.relative_rise, 3),
            "earlier_window_start": self.earlier_window_start,
            "earlier_window_end": self.earlier_window_end,
            "recent_window_start": self.recent_window_start,
            "recent_window_end": self.recent_window_end,
            "distinct_days": self.distinct_days,
        }


async def sales_by_date(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> Dict[str, int]:
    """{date_str: units_sold} for one (user, marketplace, sku). DB-headless —
    ImportedFinanceRow.quantity only, keyed by the YYYY-MM-DD date string."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.quantity).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    by_date: Dict[str, int] = {}
    for date, qty in rows:
        by_date[date] = by_date.get(date, 0) + int(qty or 0)
    return by_date


async def returns_by_date(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> Dict[str, int]:
    """{date_str: returns_qty} for one (user, marketplace, sku). DB-headless —
    ImportedReturnRow.returns_qty only. return_amount is deliberately NOT read (double-count
    discipline)."""
    rows = (await db.execute(
        select(ImportedReturnRow.date, ImportedReturnRow.returns_qty).where(
            ImportedReturnRow.user_id == user_id,
            ImportedReturnRow.marketplace == marketplace,
            ImportedReturnRow.sku == sku,
            ImportedReturnRow.date.isnot(None)))).all()
    by_date: Dict[str, int] = {}
    for date, qty in rows:
        by_date[date] = by_date.get(date, 0) + int(qty or 0)
    return by_date


def _sum_in(by_date: Dict[str, int], start: str, end: str, *, include_end: bool) -> int:
    """Observed total with start <= date < end (or <= end when include_end). ISO date strings
    sort chronologically, so lexical comparison is a valid date-window filter."""
    total = 0
    for date, v in by_date.items():
        if date >= start and (date <= end if include_end else date < end):
            total += v
    return total


def classify_returns_rise(
    sales: Dict[str, int], returns: Dict[str, int], *, marketplace: str, sku: str
) -> Optional[ReturnsDiagnosis]:
    """Confirmed self-referential return-rate rise, or None (honest absence). Observed-only.
    The observed period is defined by the SALE dates (units sold is the denominator); returns are
    counted within each window's date range."""
    if not returns:
        return None                                          # no returns data — nothing to diagnose
    sale_dates = sorted(d for d in sales.keys())
    if len(sale_dates) < MIN_OBSERVED_DAYS:
        return None                                          # thin sales history

    mid = len(sale_dates) // 2                                # split index (>=1 since len>=3)
    first_date = sale_dates[0]
    mid_date = sale_dates[mid]
    last_date = sale_dates[-1]
    if first_date >= mid_date or mid_date > last_date:
        return None                                          # windows cannot be aligned honestly

    # H1 = [first, mid); H2 = [mid, last]. Non-overlapping, chronological.
    earlier_units = _sum_in(sales, first_date, mid_date, include_end=False)
    recent_units = _sum_in(sales, mid_date, last_date, include_end=True)
    if earlier_units < MIN_WINDOW_UNITS or recent_units < MIN_WINDOW_UNITS:
        return None                                          # a window lacks meaningful sales volume

    earlier_returns = _sum_in(returns, first_date, mid_date, include_end=False)
    recent_returns = _sum_in(returns, mid_date, last_date, include_end=True)

    earlier_rate = earlier_returns / earlier_units
    recent_rate = recent_returns / recent_units
    if earlier_rate == 0:
        return None                                          # cannot compare a rise safely
    if recent_rate <= earlier_rate:
        return None                                          # flat / falling — not a rise

    relative_rise = (recent_rate - earlier_rate) / earlier_rate
    if relative_rise < LOW_RISE:
        return None                                          # below smallest band

    if relative_rise >= SEVERE_RISE:
        priority = band = "high"
    elif relative_rise >= MED_RISE:
        priority = band = "medium"
    else:
        priority = band = "low"

    return ReturnsDiagnosis(
        problem_type="return_rate_rise", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, relative_rise=relative_rise,
        earlier_return_rate=earlier_rate, recent_return_rate=recent_rate,
        earlier_returns=earlier_returns, recent_returns=recent_returns,
        earlier_units=earlier_units, recent_units=recent_units,
        earlier_window_start=first_date, earlier_window_end=mid_date,
        recent_window_start=mid_date, recent_window_end=last_date,
        distinct_days=len(sale_dates))
