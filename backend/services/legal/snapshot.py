"""
Canonical LegalSnapshot (Legal A3) — single marketplace-agnostic input bundle for
the (future) Legal Rule Engine.

Legal Navigator finds legal RISK to look into, never a verdict. This snapshot
aggregates whatever legal-relevant inputs PULT already stores about a subject
(product / listing / brand / account) and reports, HONESTLY:
  * available_inputs       — inputs that ARE present
  * missing_inputs         — inputs that are absent
  * requirement_candidates — legal requirement types that MAY apply to the subject
  * not_evaluated_reasons  — per requirement, why it cannot be evaluated yet

Honesty rules:
  * A missing input is NEVER read as "compliant" — absence of data ≠ no risk.
  * No legal conclusion, no guarantee, no asserted compliance, no score, no
    forecast, no AI.
  * `marketplace` is provenance / context only.

`status`:
  ready               — at least one requirement candidate has all required inputs
                        (the future engine could evaluate it)
  not_evaluated_ready — snapshot built, but NO candidate is evaluable yet
                        (everything is not_evaluated — this is NOT "compliant")

Pure data — no logic, no rules, no findings, no signals, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Tuple

# Legal requirement types that may apply to a subject. Closed, stable, ordered.
REQUIREMENT_CANDIDATES: Tuple[str, ...] = (
    "product_certification",
    "trademark_usage",
    "labeling_requirements",
    "marketplace_offer_terms",
    "return_policy_obligations",
    "content_claim_risk",
)

# Inputs each candidate would need before a future engine could evaluate it.
REQUIRED_INPUTS: Mapping[str, Tuple[str, ...]] = {
    "product_certification":     ("product_category", "certificate_data"),
    "trademark_usage":           ("product_title_or_brand",),
    "labeling_requirements":     ("product_category",),
    "marketplace_offer_terms":   ("marketplace", "offer_terms_data"),
    "return_policy_obligations": ("marketplace", "return_policy_data"),
    "content_claim_risk":        ("product_text",),
}


@dataclass(frozen=True)
class LegalDataUnavailable:
    """Honest negative result — a snapshot cannot even be framed. No fake data."""
    marketplace: str
    reason: str            # insufficient_data | no_db_context
    detail: Optional[str] = None


@dataclass(frozen=True)
class LegalSnapshot:
    # identity / subject
    seller_id:    str
    marketplace:  str                  # provenance / context only
    subject_type: Optional[str]        # product|listing|brand|account|sku
    subject_ref:  Optional[str]
    sku:          Optional[str]
    listing_id:   Optional[str]

    source:            str             # internal
    snapshot_created_at: datetime
    status:            str             # ready | not_evaluated_ready

    # honest input maps
    available_inputs: Tuple[str, ...]
    missing_inputs:   Tuple[str, ...]
    field_availability: Mapping[str, bool]

    # legal framing (NOT a verdict)
    requirement_candidates: Tuple[str, ...]
    not_evaluated_reasons:  Mapping[str, str]   # requirement_type → reason
