"""
Wildberries metric adapter. ALL WB-specific field names + endpoints live here,
below the metric seam. It reads raw WB API data and normalizes it to canonical
units before returning a MetricSample. Above this file, no WB terminology.

Thin foundation slice: revenue + units_sold from the WB Statistics sales feed.
Every other catalog metric is declared unsupported here; the reader then returns
an honest `adapter_not_implemented` instead of fabricating a value.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from .metric_reader import MetricSample
from .wb_client import wb_client

MARKETPLACE = "wb"


class WBMetricAdapter:
    marketplace = MARKETPLACE
    _SUPPORTED = frozenset({"revenue", "units_sold"})

    def __init__(self, sales_fetcher: Optional[Callable] = None):
        # Injectable for testing; defaults to the real WB Statistics client.
        self._fetch_sales = sales_fetcher or wb_client.get_sales

    def supports(self, metric_name: str) -> bool:
        return metric_name in self._SUPPORTED

    async def fetch(self, *, token: str, metric_name: str, entity_id,
                    window_days, now: datetime) -> MetricSample:
        days = window_days or 7
        date_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        rows = await self._fetch_sales(token=token, date_from=date_from)

        # WB sales rows key the listing by nmId. Filter to the entity if asked.
        if entity_id is not None:
            rows = [r for r in rows if str(r.get("nmId")) == str(entity_id)]

        if metric_name == "revenue":
            value = float(sum((r.get("forPay") or 0) for r in rows))  # forPay = seller payout, ₽
            unit  = "rub"
        else:  # units_sold
            value = float(len(rows))
            unit  = "units"

        return MetricSample(value=value, unit=unit, observed_at=now,
                            source="api", quality=f"n={len(rows)}")
