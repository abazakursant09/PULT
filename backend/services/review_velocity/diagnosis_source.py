"""
Review Acquisition / Social-Proof Velocity Diagnosis source (Phase 6.1) — READ-ONLY,
DB-headless.

Answers a review-acquisition STALL per (user, marketplace, sku) from the seller's OWN
observed data: the reviews_count time-series (ImportedProductRow.reviews_count across DATED
import snapshots) and the observed sales quantity (ImportedFinanceRow.quantity by date).

reviews_count is monotonic (reviews accumulate), so the diagnosis is NOT "reviews declined".
It is SELF-REFERENTIAL: the observed period is split into an earlier window H1 and a recent
window H2; each window's reviews-per-unit-sold rate is computed, and a stall is a CONFIRMED
relative slowdown of the recent rate vs the product's OWN earlier rate — "review acquisition
slowed compared with this product's own earlier observed rate while sales continued".

NEVER an absolute review-rate floor, NEVER a category benchmark, NEVER a competitor compare,
NEVER "too few reviews" as an absolute statement. Diagnoses the observed PRESENT slowdown,
NOT a forecast. Reimplemented independently — does not import from services/rating / review /
supply / money_leak / revenue.

Honest absence (emit NOTHING): fewer than MIN_SNAPSHOTS dated reviews_count snapshots; either
window below MIN_WINDOW_UNITS observed sales; earlier_rate == 0 (no reviews gained earlier —
cannot measure a slowdown); recent_rate >= earlier_rate (flat / accelerating); relative_drop
below the smallest band; or reviews_count decreasing / windows that cannot be aligned honestly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow

# ── deterministic constants (data-sufficiency gates + self-referential bands) ──
MIN_SNAPSHOTS = 3        # need ≥3 dated reviews snapshots to form two non-trivial windows
MIN_WINDOW_UNITS = 5     # each window needs ≥5 observed units sold for a stable rate (noise gate)
LOW_DROP = 0.30          # recent_rate down ≥30% vs earlier_rate → low
MED_DROP = 0.50          # ≥50% → medium
SEVERE_DROP = 0.70       # ≥70% → high


@dataclass(frozen=True)
class ReviewVelocityDiagnosis:
    problem_type: str          # review_velocity_stall
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    relative_drop: float       # (earlier_rate − recent_rate) / earlier_rate
    earlier_review_rate: float
    recent_review_rate: float
    earlier_reviews_gained: int
    recent_reviews_gained: int
    earlier_units_sold: int
    recent_units_sold: int
    earlier_window_start: str
    earlier_window_end: str
    recent_window_start: str
    recent_window_end: str
    snapshot_count: int

    @property
    def signal_key(self) -> str:
        return "review_velocity_stall"

    @property
    def insight_key(self) -> str:
        return f"review_velocity_stall:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "earlier_reviews_gained": self.earlier_reviews_gained,
            "recent_reviews_gained": self.recent_reviews_gained,
            "earlier_units_sold": self.earlier_units_sold,
            "recent_units_sold": self.recent_units_sold,
            "earlier_review_rate": round(self.earlier_review_rate, 4),
            "recent_review_rate": round(self.recent_review_rate, 4),
            "relative_drop": round(self.relative_drop, 3),
            "earlier_window_start": self.earlier_window_start,
            "earlier_window_end": self.earlier_window_end,
            "recent_window_start": self.recent_window_start,
            "recent_window_end": self.recent_window_end,
            "snapshot_count": self.snapshot_count,
        }


async def build_review_series(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> List[Tuple[str, int]]:
    """(date_str, reviews_count) per dated ImportedProductRow snapshot, oldest→newest, for
    one (user, marketplace, sku). DB-headless — ImportedProductRow only, ordered by created_at.
    Only reviews-bearing snapshots (reviews_count not None) are returned; created_at is
    reduced to its ISO date (YYYY-MM-DD) so it aligns with ImportedFinanceRow.date."""
    rows = (await db.execute(
        select(ImportedProductRow.created_at, ImportedProductRow.reviews_count).where(
            ImportedProductRow.user_id == user_id,
            ImportedProductRow.marketplace == marketplace,
            ImportedProductRow.sku == sku,
            ImportedProductRow.reviews_count.isnot(None))
        .order_by(ImportedProductRow.created_at.asc()))).all()
    return [(created_at.date().isoformat(), int(rc)) for created_at, rc in rows if created_at is not None]


async def observed_sales_by_date(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> Dict[str, int]:
    """{date_str: total_units} for one (user, marketplace, sku) from finance. DB-headless —
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


def _units_in(sales_by_date: Dict[str, int], start: str, end: str, *, include_end: bool) -> int:
    """Observed units with start <= date < end (or <= end when include_end). ISO date
    strings sort chronologically, so lexical comparison is a valid date-window filter."""
    total = 0
    for date, units in sales_by_date.items():
        if date >= start and (date <= end if include_end else date < end):
            total += units
    return total


def classify_review_velocity_stall(
    series: List[Tuple[str, int]], sales_by_date: Dict[str, int], *, marketplace: str, sku: str
) -> Optional[ReviewVelocityDiagnosis]:
    """Confirmed self-referential review-acquisition slowdown, or None (honest absence).
    Observed-only; splits the snapshot series into earlier (H1) / recent (H2) windows and
    compares each window's reviews-per-unit-sold rate."""
    if len(series) < MIN_SNAPSHOTS:
        return None                                          # thin reviews history

    mid = len(series) // 2                                    # split index (>=1 since len>=3)
    first_date, first_reviews = series[0]
    mid_date, mid_reviews = series[mid]
    last_date, last_reviews = series[-1]

    earlier_gained = mid_reviews - first_reviews
    recent_gained = last_reviews - mid_reviews
    if earlier_gained < 0 or recent_gained < 0:
        return None                                          # reviews_count decreased — bad data

    # H1 = [first_date, mid_date); H2 = [mid_date, last_date]. Non-overlapping, chronological.
    if first_date >= mid_date or mid_date > last_date:
        return None                                          # windows cannot be aligned honestly
    earlier_units = _units_in(sales_by_date, first_date, mid_date, include_end=False)
    recent_units = _units_in(sales_by_date, mid_date, last_date, include_end=True)

    if earlier_units < MIN_WINDOW_UNITS or recent_units < MIN_WINDOW_UNITS:
        return None                                          # a window lacks meaningful sales volume
    if earlier_gained == 0:
        return None                                          # earlier_rate == 0 — no slowdown to measure

    earlier_rate = earlier_gained / earlier_units
    recent_rate = recent_gained / recent_units
    if recent_rate >= earlier_rate:
        return None                                          # flat / accelerating — not a stall

    relative_drop = (earlier_rate - recent_rate) / earlier_rate
    if relative_drop < LOW_DROP:
        return None                                          # below smallest band

    if relative_drop >= SEVERE_DROP:
        priority = band = "high"
    elif relative_drop >= MED_DROP:
        priority = band = "medium"
    else:
        priority = band = "low"

    return ReviewVelocityDiagnosis(
        problem_type="review_velocity_stall", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, relative_drop=relative_drop,
        earlier_review_rate=earlier_rate, recent_review_rate=recent_rate,
        earlier_reviews_gained=earlier_gained, recent_reviews_gained=recent_gained,
        earlier_units_sold=earlier_units, recent_units_sold=recent_units,
        earlier_window_start=first_date, earlier_window_end=mid_date,
        recent_window_start=mid_date, recent_window_end=last_date,
        snapshot_count=len(series))
