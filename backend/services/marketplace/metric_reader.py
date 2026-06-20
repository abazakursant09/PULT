"""
Metric Reader — resolves a canonical metric for an entity into a normalized
sample, gating strictly on capability_registry honesty.

Flow: metric_catalog (meaning) → capability_registry (may we read it?) → adapter
(read + normalize). The reader holds NO marketplace branching beyond the adapter
registry lookup; all marketplace-specific field handling lives inside adapters,
below the seam. Returns either a MetricSample (a fact) or a MetricUnavailable
(an honest, reasoned absence — never a fabricated zero).

Scope of this layer: produce one normalized fact. No persistence side effects in
read_metric, no aggregation, no interpretation, no learning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Union

from services.marketplace import metric_catalog


@dataclass(frozen=True)
class MetricSample:
    value:       float
    unit:        str
    observed_at: datetime
    source:      str                # api | compute | forecast
    quality:     Optional[str] = None


@dataclass(frozen=True)
class MetricUnavailable:
    metric_name: str
    reason:      str                # unknown_metric | api | tariff | no_adapter | adapter_not_implemented
    detail:      Optional[str] = None


class MetricAdapter(Protocol):
    marketplace: str
    def supports(self, metric_name: str) -> bool: ...
    async def fetch(self, *, token: str, metric_name: str, entity_id, window_days, now: datetime) -> MetricSample: ...


def _adapters() -> dict[str, MetricAdapter]:
    # Lazy import: adapter module imports MetricSample from this module.
    from .wb_metric_adapter import WBMetricAdapter
    return {"wb": WBMetricAdapter()}


# Compute metrics are finance-backed (db), not marketplace-API. They bypass the
# adapter registry and never call a marketplace client.
_COMPUTE_METRICS = frozenset({"net_profit"})


async def read_metric(
    *,
    token: str,
    marketplace: str,
    metric_name: str,
    entity_id=None,
    window_days: int = 7,
    tariffs: Optional[set[str]] = None,
    now: Optional[datetime] = None,
    db=None,
    user_id=None,
) -> Union[MetricSample, MetricUnavailable]:
    now = now or datetime.utcnow()

    spec = metric_catalog.get(metric_name)
    if spec is None:
        return MetricUnavailable(metric_name, "unknown_metric")

    mp = metric_catalog.normalize_marketplace(marketplace)
    if mp is None:
        return MetricUnavailable(metric_name, "no_adapter", f"unknown marketplace: {marketplace}")

    avail = metric_catalog.availability(metric_name, marketplace, tariffs)
    if not avail.get("available"):
        return MetricUnavailable(metric_name, avail.get("status") or "unavailable", avail.get("reason"))

    # Compute (finance-backed) metrics: no marketplace adapter, no API call.
    if metric_name in _COMPUTE_METRICS:
        from .finance_metric_reader import read_net_profit
        return await read_net_profit(
            db=db, user_id=user_id, marketplace=mp, entity_id=entity_id,
            window_days=window_days, now=now,
        )

    adapter = _adapters().get(mp)
    if adapter is None:
        return MetricUnavailable(metric_name, "no_adapter")
    if not adapter.supports(metric_name):
        # Registry says available, but no adapter read is wired yet. Honest gap,
        # NOT a fabricated value — exactly what the capability matrix surfaces.
        return MetricUnavailable(metric_name, "adapter_not_implemented")

    return await adapter.fetch(
        token=token, metric_name=metric_name, entity_id=entity_id,
        window_days=window_days, now=now,
    )


def build_observation(
    sample: MetricSample,
    *,
    user_id: str,
    entity_grain: str,
    entity_id,
    metric_name: str,
    marketplace: Optional[str],
    window_days: Optional[int],
):
    """Construct (do not persist) an Observation ORM row from a sample."""
    from models.observation import Observation
    return Observation(
        user_id=user_id,
        entity_grain=entity_grain,
        entity_id=str(entity_id) if entity_id is not None else None,
        metric_name=metric_name,
        marketplace=marketplace,
        value=sample.value,
        unit=sample.unit,
        window_days=window_days,
        observed_at=sample.observed_at,
        source=sample.source,
        quality=sample.quality,
    )
