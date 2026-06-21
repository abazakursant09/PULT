"""
Canonical GrowthSnapshot (Growth A3) — single marketplace-agnostic input to the
(future) Growth Rule Engine.

Growth Engine finds opportunities, not defects. This snapshot aggregates data
PULT ALREADY STORES across contours (finance, advertising, SEO, reviews,
operations) into one read-only frozen record. Rules will consume this snapshot
only; they never read source tables / MP APIs directly.

Honesty rules:
  * Missing data is marked in `field_availability` — NEVER faked, NEVER zero-filled.
  * No forecast, no market trend, no competitor data, no external API, no score.
  * `marketplace` is provenance / context only.

Pure data — no logic, no rules, no AI, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class GrowthDataUnavailable:
    """Honest negative result — a snapshot cannot be built. No fake data."""
    marketplace: str
    reason: str            # finance_missing | insufficient_data | no_db_context
    detail: Optional[str] = None


@dataclass(frozen=True)
class GrowthSnapshot:
    # identity
    listing_id:  Optional[str]
    marketplace: str                 # provenance / context only
    sku:         Optional[str]
    captured_at: datetime
    source:      str                 # internal

    # finance
    revenue:     Optional[float]
    net_profit:  Optional[float]
    margin:      Optional[float]     # percent; net_profit / revenue * 100
    units_sold:  Optional[int]

    # advertising
    ad_spend:    Optional[float]
    drr:         Optional[float]     # percent; ad_spend / revenue * 100

    # seo (cross-contour live signal counts)
    active_seo_signals:   Optional[int]
    critical_seo_signals: Optional[int]

    # reviews (cross-contour live signal counts)
    active_review_signals: Optional[int]
    risk_review_signals:   Optional[int]

    # operations
    stock_units: Optional[int]
    days_to_oos: Optional[float]

    # context
    category:    Optional[str]
    margin_band: Optional[str]       # negative | low | medium | high (classified, not forecast)

    # honest availability map
    field_availability: Mapping[str, bool]
