"""
Canonical AdvertisingSnapshot (Advertising A3) — the single marketplace-agnostic
input to the (future) Advertising Rule Engine.

Advertising is analysed ONLY through impact on profit / margin / stock / listing.
Rules must never read ImportedFinanceRow (or any MP API) directly — they consume
this snapshot. All thresholds live in AdvertisingThresholds and arrive from
outside; AdvertisingThresholds has NO defaults, so the core cannot fabricate a
limit. Missing data is marked honestly in field_availability (False) — never
faked. `marketplace` is provenance / context only.

Pure data — no logic, no rules, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class AdvertisingThresholds:
    """All advertising thresholds — supplied from outside, never by core.
    No defaults: rules that need a missing threshold become not_evaluated."""
    max_drr: float
    min_revenue_for_signal: float
    min_ad_spend_for_signal: float
    low_margin_threshold: float
    low_stock_units: int
    oos_risk_days: float


@dataclass(frozen=True)
class AdvertisingDataUnavailable:
    """Honest negative result — a snapshot cannot be built. No fake data."""
    marketplace: str
    reason: str            # finance_missing | thresholds_missing | listing_not_found |
                           # insufficient_data | no_db_context
    detail: Optional[str] = None


@dataclass(frozen=True)
class AdvertisingSnapshot:
    """Canonical money/operations/seo view of a listing for advertising decisions."""
    # identity
    listing_id:  Optional[str]
    marketplace: str                 # provenance / context only
    sku:         Optional[str]
    captured_at: datetime
    source:      str                 # finance | api | manual

    # money (None when unavailable)
    revenue:     Optional[float]
    net_profit:  Optional[float]
    ad_spend:    Optional[float]
    orders:      Optional[int]
    units_sold:  Optional[int]
    margin:      Optional[float]     # % = net_profit / revenue * 100
    drr:         Optional[float]     # % = ad_spend / revenue * 100

    # operations
    stock_units: Optional[int]
    days_to_oos: Optional[float]

    # seo (cross-contour)
    active_seo_problems:   Optional[int]
    critical_seo_problems: Optional[int]

    # context
    category:    Optional[str]
    price_band:  Optional[str]
    margin_band: Optional[str]

    # thresholds (None → threshold-dependent rules not_evaluated; never invented)
    thresholds:  Optional[AdvertisingThresholds]

    # honest map of which fields are actually available
    field_availability: Mapping[str, bool]
