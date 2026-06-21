"""
Canonical Insight Type registry (Decision Outcome A2) — single source of truth for
every engine lifecycle signal that the future Signal→Decision bridge may consume.

For each contour signal type it declares:
  * contour / signal_key (the `<prefix>_<type>` stored on the signal row)
  * insight_type (bare rule key)
  * key_arity — number of ':'-segments in the engine insight_key
  * three_part_compatible — does insight_key match the canonical
    `<type>:<marketplace>:<sku>` anchor (services/insight_keys.py)?
  * carries_review_id — Review embeds a 4th segment (review_id)
  * default_metric_key — what a later measurement sprint would observe (NAME only,
    never a number / forecast)

Pure declaration — no DB, no key-building, no logic. This is the contract A6/A7
will read; the only purpose in A2 is to FIX the canonical set and SURFACE the
incompatible (non-3-part) keys for an explicit normalization decision.

Insight_key formats found in master (e4968cc):
  seo_<t>:<mp>:<sku>            (3-part, compatible)
  adv_<t>:<mp>:<sku>            (3-part, compatible)
  growth_<t>:<mp>:<sku>         (3-part, compatible)
  legal_<t>:<mp>:<sku>          (3-part, compatible)
  rev_<t>:<mp>:<sku>:<review_id> (4-part, INCOMPATIBLE — carries review_id)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# contour → engine signal_key prefix
PREFIX = {
    "seo": "seo", "advertising": "adv", "review": "rev", "growth": "growth", "legal": "legal",
}

# contour → metric NAME a future measurement would observe (no number, no forecast)
_DEFAULT_METRIC = {
    "seo": "search_visibility",
    "advertising": "ad_profit_impact",
    "review": "reputation_handling",
    "growth": "growth_realized",
    "legal": "legal_risk_open",
}

# bare insight types per contour (RULE_REGISTRY keys, master e4968cc)
_TYPES = {
    "seo": (
        "required_attributes_missing", "wrong_category_placement", "title_too_short",
        "title_too_long", "attributes_incomplete", "filter_attributes_missing",
        "variant_attributes_missing", "attribute_values_invalid", "description_missing",
        "description_too_short", "content_completeness_low", "media_below_minimum",
    ),
    "advertising": (
        "ad_destroying_profit", "ad_spend_without_sales", "ad_on_unprofitable_product",
        "ad_on_low_stock", "ad_on_bad_listing", "ad_on_oos_risk",
    ),
    "review": (
        "unanswered_negative_review", "unanswered_attention_review", "safe_review_can_reply",
        "five_star_without_text", "complaint_detected", "already_answered",
    ),
    "growth": (
        "profitable_ad_candidate", "seo_leverage_candidate", "review_leverage_candidate",
        "stock_expansion_candidate", "margin_expansion_candidate",
    ),
    "legal": (
        "product_certification", "trademark_usage", "labeling_requirements",
        "marketplace_offer_terms", "return_policy_obligations", "content_claim_risk",
    ),
}

# Review is the only contour whose insight_key carries a 4th segment (review_id).
_FOUR_PART_CONTOURS = frozenset({"review"})


@dataclass(frozen=True)
class CanonicalInsightType:
    contour: str
    signal_key: str            # <prefix>_<insight_type>
    insight_type: str
    key_arity: int             # ':'-segments in the engine insight_key
    three_part_compatible: bool
    carries_review_id: bool
    default_metric_key: str
    # Executor action binding for promotion. Intentionally None in A2/A3 — the real
    # one-click action_key is capability-gated and decided at the bridge (A6); the
    # engine signal's own recommended_action_key is advisory, not an executor key.
    action_key: str | None = None


def _build() -> Tuple[CanonicalInsightType, ...]:
    out = []
    for contour, types in _TYPES.items():
        prefix = PREFIX[contour]
        four = contour in _FOUR_PART_CONTOURS
        for t in types:
            out.append(CanonicalInsightType(
                contour=contour,
                signal_key=f"{prefix}_{t}",
                insight_type=t,
                key_arity=4 if four else 3,
                three_part_compatible=not four,
                carries_review_id=four,
                default_metric_key=_DEFAULT_METRIC[contour],
            ))
    return tuple(out)


CANONICAL_INSIGHT_TYPES: Tuple[CanonicalInsightType, ...] = _build()

# convenience lookups (pure, no state)
BY_SIGNAL_KEY = {c.signal_key: c for c in CANONICAL_INSIGHT_TYPES}
CONTOURS = tuple(_TYPES.keys())


def incompatible_signal_keys() -> Tuple[str, ...]:
    """Engine signal_keys whose insight_key is NOT 3-part canonical (bridge must
    normalize these before promotion). Today: only the Review contour."""
    return tuple(c.signal_key for c in CANONICAL_INSIGHT_TYPES if not c.three_part_compatible)
