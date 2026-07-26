"""Derived-metric completeness (PULT-LAUNCH-1.4.5H).

Profit and margin combine money the API CAN provide (revenue, fees, logistics) with money it never
can (cost of goods, external ad spend). When a required manual input is missing, the exact figure is
suppressed and the result is marked incomplete — a missing cost of goods is 'no data', never 0, so a
recommendation never quotes a precise profit it cannot actually know.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class DerivedProfit:
    value: Optional[Decimal]           # None when suppressed (a required input is missing)
    completeness: str                  # complete | incomplete | no_data
    missing_fields: list[str] = field(default_factory=list)


def profit(*, revenue: Optional[Decimal], marketplace_fees: Optional[Decimal] = None,
           logistics: Optional[Decimal] = None, cogs: Optional[Decimal] = None,
           external_ad_spend: Optional[Decimal] = None) -> DerivedProfit:
    """Net profit = the SIGNED sum of its components: revenue (+) plus fees / logistics / cogs /
    external ad spend, each already carrying its own sign (costs are negative, matching how signed
    money is stored in MarketplaceOperation).

    cost of goods and external ad spend are structurally manual; if either is missing, the exact
    profit is NOT computed (value=None) and the metric is 'incomplete' with the gap named, so nothing
    downstream can present a precise-but-wrong number. Missing revenue with nothing else = no_data."""
    missing: list[str] = []
    if cogs is None:
        missing.append("cogs")
    if external_ad_spend is None:
        missing.append("ad_spend")

    if revenue is None and not any(v is not None for v in (marketplace_fees, logistics, cogs)):
        return DerivedProfit(None, "no_data", missing)
    if missing:
        # A required manual input is absent — never fill it with 0; suppress the exact figure.
        return DerivedProfit(None, "incomplete", missing)

    total = (revenue or Decimal("0")) + (marketplace_fees or Decimal("0")) \
        + (logistics or Decimal("0")) + (cogs or Decimal("0")) + (external_ad_spend or Decimal("0"))
    return DerivedProfit(total, "complete", [])
