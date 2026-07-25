"""
Rating / Reputation Health Diagnosis source (Phase 5.1) — READ-ONLY, DB-headless.

Answers aggregate rating DECLINE per (user, marketplace, sku) from the seller's OWN
observed ImportedProductRow.rating across DATED import snapshots (imports are insert-only,
so each import adds a new dated row → a real rating time-series). Diagnoses the observed
PRESENT decline (baseline snapshot → latest snapshot), NOT a forecast.

DISTINCT from the Review contour (which handles individual ReviewResponse workflow). This
is the aggregate rating-TREND diagnosis; it does NOT import from or modify Review, and does
NOT reuse review_signal. Reimplemented independently — does not import from services/supply
/ money_leak / revenue.

Confirmed decline only: requires MIN_SNAPSHOTS distinct dated rating snapshots; the latest
rating must be below the baseline (oldest) by at least the smallest band; and the
second-newest snapshot must also be below baseline (so a single-snapshot blip on a flat
history is rejected — not a confirmed decline).

Honest absence (emit NOTHING): < MIN_SNAPSHOTS rating snapshots; latest snapshot missing
rating; flat or rising rating; drop below the smallest band; single-blip not confirmed by
the prior snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_product import ImportedProductRow

# ── deterministic constants (no fabricated numbers, no external tuning) ───────
MIN_SNAPSHOTS = 3
LOW_DROP = 0.10          # drop ≥0.10 and <0.20 → low
MED_DROP = 0.20          # drop ≥0.20 and <0.40 → medium
SEVERE_DROP = 0.40       # drop ≥0.40 → high


@dataclass(frozen=True)
class RatingDiagnosis:
    problem_type: str          # rating_decline
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    rating_drop: float         # observed baseline − latest (positive = decline)
    latest_rating: float
    baseline_rating: float
    snapshot_count: int
    latest_reviews_count: Optional[int]
    baseline_reviews_count: Optional[int]

    @property
    def signal_key(self) -> str:
        return "rating_decline"

    @property
    def insight_key(self) -> str:
        return f"rating_decline:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "latest_rating": round(self.latest_rating, 3),
            "baseline_rating": round(self.baseline_rating, 3),
            "rating_drop": round(self.rating_drop, 3),
            "snapshot_count": self.snapshot_count,
            "latest_reviews_count": self.latest_reviews_count,
            "baseline_reviews_count": self.baseline_reviews_count,
        }


async def build_rating_series(
    db: AsyncSession, user_id: str, marketplace: str, sku: str,
    *, source: str = "csv", store_id: str | None = None,
) -> List[Tuple[Optional[float], Optional[int]]]:
    """(rating, reviews_count) per dated ImportedProductRow snapshot, oldest→newest, for one
    (user, marketplace, sku). DB-headless — ImportedProductRow only, ordered by created_at.

    Source-single (PULT-LAUNCH-1.4.5H): built from ONE `source` (default 'csv') so CSV and API
    snapshots never interleave into a false rating trend; `store_id` scopes to one store."""
    conds = [
        ImportedProductRow.user_id == user_id,
        ImportedProductRow.marketplace == marketplace,
        ImportedProductRow.sku == sku,
        ImportedProductRow.source == source,
    ]
    if store_id is not None:
        conds.append(ImportedProductRow.marketplace_store_id == store_id)
    rows = (await db.execute(
        select(ImportedProductRow.rating, ImportedProductRow.reviews_count).where(*conds)
        .order_by(ImportedProductRow.created_at.asc()))).all()
    return [(rating, reviews) for rating, reviews in rows]


def classify_rating_decline(
    series: List[Tuple[Optional[float], Optional[int]]], *, marketplace: str, sku: str
) -> Optional[RatingDiagnosis]:
    """Confirmed aggregate rating decline, or None (honest absence). Observed-only."""
    rated = [(r, rc) for (r, rc) in series if r is not None]     # rating-bearing snapshots
    if len(rated) < MIN_SNAPSHOTS:
        return None                                              # thin history

    baseline_rating, baseline_reviews = rated[0]
    latest_rating, latest_reviews = rated[-1]
    prev_rating = rated[-2][0]                                   # second-newest

    drop = baseline_rating - latest_rating
    if drop < LOW_DROP:
        return None                                             # flat / rising / below smallest band
    if prev_rating >= baseline_rating:
        return None                                             # single-snapshot blip — not confirmed

    if drop >= SEVERE_DROP:
        priority = band = "high"
    elif drop >= MED_DROP:
        priority = band = "medium"
    else:
        priority = band = "low"

    return RatingDiagnosis(
        problem_type="rating_decline", marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, rating_drop=drop,
        latest_rating=latest_rating, baseline_rating=baseline_rating,
        snapshot_count=len(rated), latest_reviews_count=latest_reviews,
        baseline_reviews_count=baseline_reviews)
