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
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow
from .reason_taxonomy import normalize_return_reason, DIAGNOSED_BUCKETS, UNSPECIFIED

# ── deterministic constants (data-sufficiency gates + self-referential bands) ──
MIN_OBSERVED_DAYS = 3      # need ≥3 distinct observed sale days to form two windows
MIN_WINDOW_UNITS = 5       # each window needs ≥5 units sold for a stable rate (noise gate)
LOW_RISE = 0.25           # recent rate up ≥25% vs earlier rate → low
MED_RISE = 0.50           # ≥50% → medium
SEVERE_RISE = 1.00        # ≥100% (doubled) → high

# ── reason-share gates (Returns-Reason Diagnosis, counts-only) ────────────────
MIN_RETURN_DAYS = 3            # need ≥3 distinct observed RETURN days to form two windows
MIN_TOTAL_RETURNS_WINDOW = 5   # each window needs ≥5 total returns for a stable composition
MIN_BUCKET_RETURNS_WINDOW = 3  # a diagnosed bucket needs ≥3 returns in BOTH windows (rare gate)
MAX_UNSPECIFIED_SHARE = 0.5    # if >50% of returns carry no reason, composition is unreliable →
                               # honest absence for the WHOLE sku (cannot diagnose an unreported mix)


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


# ══════════════════════════════════════════════════════════════════════════════
# Returns-Reason Diagnosis (reason_share_rise_<bucket>) — observed COMPOSITION drift.
#
# ORTHOGONAL to return_rate_rise: that measures return FREQUENCY (returns ÷ units sold);
# this measures the COMPOSITION of returns (bucket returns ÷ TOTAL returns). A sku can shift
# its reason mix with a flat return rate, or raise its rate with a stable mix — different
# numerator AND denominator, different question. Both are COUNTS-ONLY (returns_qty), so the
# double-count discipline holds: NEVER net_profit, NEVER return_amount.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReasonShareDiagnosis:
    problem_type: str          # reason_share_rise_<bucket>
    bucket: str
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    relative_rise: float       # (recent_share − earlier_share) / earlier_share
    earlier_bucket_returns: int
    recent_bucket_returns: int
    earlier_total_returns: int
    recent_total_returns: int
    earlier_share: float
    recent_share: float
    earlier_window_start: str
    earlier_window_end: str
    recent_window_start: str
    recent_window_end: str
    distinct_return_days: int

    @property
    def signal_key(self) -> str:
        return f"returns_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"returns_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        # COUNTS/FREQUENCY only — NO net_profit, NO return_amount (double-count discipline)
        return {
            "bucket": self.bucket,
            "earlier_bucket_returns": self.earlier_bucket_returns,
            "recent_bucket_returns": self.recent_bucket_returns,
            "earlier_total_returns": self.earlier_total_returns,
            "recent_total_returns": self.recent_total_returns,
            "earlier_share": round(self.earlier_share, 4),
            "recent_share": round(self.recent_share, 4),
            "relative_rise": round(self.relative_rise, 3),
            "earlier_window_start": self.earlier_window_start,
            "earlier_window_end": self.earlier_window_end,
            "recent_window_start": self.recent_window_start,
            "recent_window_end": self.recent_window_end,
            "distinct_return_days": self.distinct_return_days,
        }


async def returns_by_reason_by_date(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> Dict[str, Dict[str, int]]:
    """{date_str: {bucket: returns_qty}} for one (user, marketplace, sku). DB-headless —
    ImportedReturnRow.returns_qty + reason ONLY. return_amount is deliberately NOT read
    (double-count discipline). Every row's reported reason is normalized into a bucket
    (null/empty → 'unspecified', unmatched → 'other')."""
    rows = (await db.execute(
        select(ImportedReturnRow.date, ImportedReturnRow.returns_qty,
               ImportedReturnRow.reason).where(
            ImportedReturnRow.user_id == user_id,
            ImportedReturnRow.marketplace == marketplace,
            ImportedReturnRow.sku == sku,
            ImportedReturnRow.date.isnot(None)))).all()
    by_date: Dict[str, Dict[str, int]] = {}
    for date, qty, reason in rows:
        bucket = normalize_return_reason(reason)
        day = by_date.setdefault(date, {})
        day[bucket] = day.get(bucket, 0) + int(qty or 0)
    return by_date


def _band(rel: float) -> str:
    if rel >= SEVERE_RISE:
        return "high"
    if rel >= MED_RISE:
        return "medium"
    return "low"


def _window_totals(by_date: Dict[str, Dict[str, int]], dates) -> Dict[str, int]:
    """Sum returns per bucket across a set of dates."""
    out: Dict[str, int] = {}
    for d in dates:
        for bucket, qty in by_date.get(d, {}).items():
            out[bucket] = out.get(bucket, 0) + qty
    return out


def classify_reason_share_drifts(
    by_reason: Dict[str, Dict[str, int]], *, marketplace: str, sku: str
) -> List[ReasonShareDiagnosis]:
    """0..N confirmed reason-share rises (one per rising DIAGNOSED bucket). Observed COMPOSITION
    drift, self-referential. Honest absence → empty list. Counts-only.

    Guards: ≥MIN_RETURN_DAYS distinct return days; each window ≥MIN_TOTAL_RETURNS_WINDOW returns;
    unspecified share ≤MAX_UNSPECIFIED_SHARE across the observed period; each diagnosed bucket
    needs ≥MIN_BUCKET_RETURNS_WINDOW in BOTH windows; earlier_share>0; recent_share>earlier_share;
    relative_rise ≥LOW_RISE. 'other'/'unspecified' count in the denominator but never signal."""
    return_dates = sorted(d for d, buckets in by_reason.items() if sum(buckets.values()) > 0)
    if len(return_dates) < MIN_RETURN_DAYS:
        return []

    mid = len(return_dates) // 2                       # split index (>=1 since len>=3)
    first_date, mid_date, last_date = return_dates[0], return_dates[mid], return_dates[-1]
    if first_date >= mid_date or mid_date > last_date:
        return []                                       # windows cannot be aligned honestly

    earlier_dates = [d for d in return_dates if first_date <= d < mid_date]
    recent_dates = [d for d in return_dates if mid_date <= d <= last_date]

    earlier = _window_totals(by_reason, earlier_dates)
    recent = _window_totals(by_reason, recent_dates)
    total_earlier = sum(earlier.values())
    total_recent = sum(recent.values())
    if total_earlier < MIN_TOTAL_RETURNS_WINDOW or total_recent < MIN_TOTAL_RETURNS_WINDOW:
        return []                                       # a window lacks meaningful return volume

    # unreported-reason guard: if reasons are largely missing, composition is unreliable
    unspecified_total = earlier.get(UNSPECIFIED, 0) + recent.get(UNSPECIFIED, 0)
    grand_total = total_earlier + total_recent
    if grand_total > 0 and unspecified_total / grand_total > MAX_UNSPECIFIED_SHARE:
        return []                                       # honest absence for the whole sku

    out: List[ReasonShareDiagnosis] = []
    for bucket in DIAGNOSED_BUCKETS:                    # named buckets only — never other/unspecified
        eb = earlier.get(bucket, 0)
        rb = recent.get(bucket, 0)
        if eb < MIN_BUCKET_RETURNS_WINDOW or rb < MIN_BUCKET_RETURNS_WINDOW:
            continue                                    # rare bucket — not enough to diagnose
        share_earlier = eb / total_earlier
        share_recent = rb / total_recent
        if share_earlier <= 0 or share_recent <= share_earlier:
            continue                                    # flat / falling composition
        rel = (share_recent - share_earlier) / share_earlier
        if rel < LOW_RISE:
            continue                                    # below smallest band
        out.append(ReasonShareDiagnosis(
            problem_type=f"reason_share_rise_{bucket}", bucket=bucket,
            marketplace=marketplace, sku=sku, priority_level=_band(rel), effect_band=_band(rel),
            relative_rise=rel, earlier_bucket_returns=eb, recent_bucket_returns=rb,
            earlier_total_returns=total_earlier, recent_total_returns=total_recent,
            earlier_share=share_earlier, recent_share=share_recent,
            earlier_window_start=first_date, earlier_window_end=mid_date,
            recent_window_start=mid_date, recent_window_end=last_date,
            distinct_return_days=len(return_dates)))
    return out
