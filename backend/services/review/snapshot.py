"""
Canonical ReviewSnapshot (Review A3) — single marketplace-agnostic input to the
(future) Review Rule Engine.

Review Assistant is a reputation contour, NOT an autoresponder. Rules consume this
snapshot only; they never read review tables / MP APIs directly. `safety_category`
+ `allowed_modes` + `default_mode` come from the deterministic ReviewSafetyPolicy
(AUTO never allowed for RISK). Missing data is marked honestly in
field_availability — never faked. `marketplace` is provenance / context only.

Pure data — no logic, no rules, no AI, no reply generation, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class ReviewDataUnavailable:
    """Honest negative result — a snapshot cannot be built. No fake data."""
    marketplace: str
    reason: str            # review_missing | listing_missing | insufficient_data | no_db_context
    detail: Optional[str] = None


@dataclass(frozen=True)
class ReviewSnapshot:
    # identity
    listing_id:  Optional[str]
    marketplace: str                 # provenance / context only
    sku:         Optional[str]
    captured_at: datetime
    source:      str                 # reviews | api | manual

    # review
    review_id:         Optional[str]
    rating:            Optional[int]
    text:              Optional[str]
    has_text:          bool
    created_at:        Optional[datetime]
    answered:          bool
    answer_text:       Optional[str]
    answer_created_at: Optional[datetime]

    # product context
    product_name: Optional[str]
    brand:        Optional[str]
    category:     Optional[str]

    # safety (from ReviewSafetyPolicy — deterministic)
    safety_category: Optional[str]            # SAFE | ATTENTION | RISK
    allowed_modes:   Tuple[str, ...]          # subset of off/manual_approval/auto/manual_only
    default_mode:    Optional[str]

    # honest availability map
    field_availability: Mapping[str, bool]
