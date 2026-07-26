"""Authoritative API dataset per (marketplace, metric) — PULT-LAUNCH-1.4.5H.

The whole point: inside ONE marketplace a single real event often appears in more than one feed. WB
reports a sale in BOTH its sales feed and its finance report; Ozon reports a delivery as a posting
AND as a finance transaction. If revenue were summed from both, one sale would count twice. This map
names the ONE authoritative feed (provider_dataset) and the operation_type(s) that carry each money
or operational metric, so the money reader reads from exactly one place.

Hard rules encoded here (and enforced by tests):
  * revenue and money-returns come ONLY from the finance dataset — never from sales/postings.
  * an Ozon posting is operational (an order), never a money sale.
  * return FREQUENCY (a count) and return MONEY are different metrics from different datasets and are
    never both counted as the same thing.
  * 'legacy' is never authoritative for anything — pre-1.4.5H rows never auto-enter a total.
  * cost of goods and external ad spend have NO API dataset — they are CSV/manual only.
  * the dataset is chosen by this map, NEVER by the sign of an amount.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Metric classes.
SNAPSHOT_METRICS = frozenset({"catalog", "card_content", "price", "stock"})
# Metrics derived from MarketplaceOperation rows over a period.
OPERATION_METRICS = frozenset({
    "orders", "revenue", "marketplace_fees", "logistics", "penalties", "deductions", "returns",
})
# Metrics the marketplace API structurally cannot know — always CSV/manual.
MANUAL_ONLY_METRICS = frozenset({"cogs", "ad_spend"})


@dataclass(frozen=True)
class DatasetAuthority:
    """The one feed + operation_type(s) a metric is counted from for a marketplace."""
    datasets: tuple[str, ...]         # provider_dataset value(s); Ozon orders span fbo+fbs
    operation_types: tuple[str, ...]  # which MarketplaceOperation.operation_type carry it
    is_money: bool                    # True = summed as Decimal money; False = counted (frequency)


# (marketplace, metric_type) -> DatasetAuthority. Absence ⇒ the API is NOT an authoritative source
# for that metric on that marketplace (e.g. every Yandex money metric — G2 finance is blocked).
_AUTHORITY: dict[tuple[str, str], DatasetAuthority] = {}


def _put(marketplace: str, metric: str, datasets, operation_types, is_money: bool) -> None:
    _AUTHORITY[(marketplace, metric)] = DatasetAuthority(
        tuple(datasets), tuple(operation_types), is_money)


for _mp in ("wildberries", "ozon"):
    # Money metrics — all from the finance report, each its own operation_type. Never from sales/postings.
    _put(_mp, "revenue", ["finance"], ["sale"], True)
    _put(_mp, "marketplace_fees", ["finance"], ["commission"], True)
    _put(_mp, "logistics", ["finance"], ["logistics"], True)
    _put(_mp, "penalties", ["finance"], ["penalty"], True)
    _put(_mp, "deductions", ["finance"], ["deduction"], True)

# Orders — operational only, never revenue. WB orders feed; Ozon BOTH posting feeds; Yandex orders.
_put("wildberries", "orders", ["orders"], ["order"], False)
_put("ozon", "orders", ["fbo_postings", "fbs_postings"], ["order"], False)
_put("yandex", "orders", ["orders"], ["order"], False)

# Return FREQUENCY (a count, not money). WB carries the return event in its sales feed; Ozon and
# Yandex have a dedicated returns feed. Money-returns live inside finance and are NOT this metric.
_put("wildberries", "returns", ["sales"], ["return"], False)
_put("ozon", "returns", ["returns"], ["return"], False)
_put("yandex", "returns", ["returns"], ["return"], False)

# Yandex money metrics (revenue/fees/logistics/penalties/deductions) are DELIBERATELY absent:
# the united-netting finance report schema is unconfirmed (G2 BLOCKED_EXTERNAL_SCHEMA).


def authoritative(marketplace: str, metric_type: str) -> Optional[DatasetAuthority]:
    """The one authoritative API dataset for this metric, or None if the API is not a source for it
    on this marketplace (manual-only metric, or an unsupported marketplace/metric like Yandex money)."""
    if metric_type in MANUAL_ONLY_METRICS:
        return None
    return _AUTHORITY.get((marketplace, metric_type))


def api_supported(marketplace: str, metric_type: str) -> bool:
    """Whether the API can be an authoritative source for this (marketplace, metric).

    True for snapshot metrics (handled by the snapshot readers, not this map) and for operation
    metrics with an authority entry. False for manual-only metrics and unsupported combinations
    (notably every Yandex money metric)."""
    if metric_type in MANUAL_ONLY_METRICS:
        return False
    if metric_type in SNAPSHOT_METRICS:
        return True
    return (marketplace, metric_type) in _AUTHORITY
