"""
Action → Metric binding (Decision Outcome enabler).

Bridges the WRITE side (action_catalog: what a seller does) to the READ side
(metric_catalog: the canonical metric that action is expected to move). This is
the load-bearing prerequisite for Decision Outcome: a validation job cannot
measure a decision's effect without knowing WHICH metric to read.

Doctrine:
- Marketplace-agnostic. Both `action_key` and `metric_name` are canonical;
  no WB/Ozon/YM terminology.
- Declares INTENT only (the metric an action targets). Whether that metric is
  actually readable is owned by capability_registry + adapter coverage — not
  asserted here. An action may be bound yet currently uncloseable; that is
  surfaced honestly downstream, never faked.
- One target metric per action: the primary effect the decision optimizes.
"""
from __future__ import annotations

from typing import Optional

from services.marketplace import action_catalog, metric_catalog

# action_type (executor key) → canonical metric_name it primarily moves.
_BINDING: dict[str, str] = {
    "set_price":                "revenue",        # price change → sales/выручка
    "ad_set_bid":               "ad_cost_ratio",  # bid change → ДРР efficiency
    "ad_set_state":             "ad_cost_ratio",  # start/pause → ДРР efficiency
    "update_card":              "ctr",            # SEO/content rebuild → CTR
    "publish_review_response":  "rating",         # reputation action → rating
}


def target_metric(action_key: Optional[str]) -> Optional[str]:
    """Canonical metric_name an action targets, or None if unbound."""
    if not action_key:
        return None
    return _BINDING.get(action_key)


def known_bindings() -> dict[str, str]:
    return dict(_BINDING)


def is_measurable(action_key: Optional[str]) -> bool:
    """
    True only if the action is bound AND its target metric exists in the
    catalog. Does NOT assert the metric is currently readable (adapter/
    capability coverage is resolved at read time, honestly).
    """
    metric = target_metric(action_key)
    return metric is not None and metric_catalog.get(metric) is not None
