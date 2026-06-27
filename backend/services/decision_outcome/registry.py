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
    "pricing": "pricing",
}

# contour → metric NAME a future measurement would observe (no number, no forecast)
_DEFAULT_METRIC = {
    "seo": "search_visibility",
    "advertising": "ad_profit_impact",
    "review": "reputation_handling",
    "growth": "growth_realized",
    "legal": "legal_risk_open",
    # pricing/margin problems are measured on profit (A3-pre): a price fix can raise
    # margin while revenue/units fall, so net_profit is the honest target.
    "pricing": "net_profit",
}

# Per-insight-type metric override (A2.2-bind). The direct-overspend advertising
# signals are bound to ad_set_state (campaign pause); their honest observed effect
# is advertising efficiency — ДРР — so they measure on ad_cost_ratio (lower-better),
# not the contour-default ad_profit_impact. Registry-layer routing only; the Effect
# Measurement lifecycle is unchanged (it still reads default_metric_key).
_METRIC_OVERRIDE = {
    "ad_destroying_profit": "ad_cost_ratio",
    "ad_spend_without_sales": "ad_cost_ratio",
    "ad_on_unprofitable_product": "ad_cost_ratio",
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
    # Pricing/margin (A3-pre) — observed finance-backed margin problems. Advice-only
    # for now; the set_price binding is a later sprint (A3-bind).
    "pricing": (
        "negative_margin", "margin_below_target", "price_below_floor",
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
    # Executor binding for promotion. `action_key` is the PRIMARY (first) executable
    # lever — kept for backward compatibility. `action_keys` is the full set of
    # admissible levers (Canonical Alternatives): each becomes its own Candidate →
    # Decision under the same insight_key. Single-action signals have
    # action_keys == (action_key,); advice-only signals have both empty/None.
    action_key: str | None = None
    action_keys: Tuple[str, ...] = ()


# Executor action binding (A3 of Action Catalog Expansion). The single source of
# truth is now services/action_binding/registry.ACTION_BINDINGS. A signal type
# receives an action_key ONLY when its binding is genuinely executable:
#   bindable AND action_key is set AND binding_status == "bound" AND the action_key
#   is a real action_catalog action AND safety_class != auto_forbidden.
# Everything else stays None (advice_only) — no fabricated action.
from services.marketplace import action_catalog as _catalog
from services.action_binding.registry import (
    bindings_for as _bindings_for, BOUND as _BOUND, AUTO_FORBIDDEN as _AUTO_FORBIDDEN,
)


def _is_executable(b) -> bool:
    return bool(b.bindable and b.action_key and b.binding_status == _BOUND
                and b.action_key in _catalog.known_actions()
                and b.safety_class != _AUTO_FORBIDDEN)


def _resolve_action_keys(contour: str, insight_type: str, signal_key: str) -> Tuple[str, ...]:
    """All admissible executor action_keys for one signal type, primary first.
    Empty when advice-only. De-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for b in _bindings_for(contour, insight_type, signal_key):
        if _is_executable(b) and b.action_key not in seen:
            seen[b.action_key] = None
    return tuple(seen)


def _build() -> Tuple[CanonicalInsightType, ...]:
    out = []
    for contour, types in _TYPES.items():
        prefix = PREFIX[contour]
        four = contour in _FOUR_PART_CONTOURS
        for t in types:
            sk = f"{prefix}_{t}"
            aks = _resolve_action_keys(contour, t, sk)
            out.append(CanonicalInsightType(
                contour=contour,
                signal_key=sk,
                insight_type=t,
                key_arity=4 if four else 3,
                three_part_compatible=not four,
                carries_review_id=four,
                default_metric_key=_METRIC_OVERRIDE.get(t, _DEFAULT_METRIC[contour]),
                action_key=aks[0] if aks else None,   # primary (backward compatible)
                action_keys=aks,
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
