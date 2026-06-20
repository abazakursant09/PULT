"""
Metric Catalog — canonical, marketplace-agnostic metric definitions (read side).

Symmetric to `action_catalog`: action_catalog normalizes *writes*, this
normalizes the *meaning* of metrics. It maps a canonical metric_name to its
unit / direction / aggregation / grain and to the `capability_registry` key that
governs WHETHER any marketplace can serve it.

Doctrine:
- NO marketplace terminology lives here. A metric is a semantic, not an API
  field. WB-CTR / Ozon-CTR / YM-CTR all collapse to `ctr`.
- Availability / freshness / scope / tariff are NOT re-encoded — they are
  inherited from `services.capability_registry` via `capability_key` (one
  source of truth, §6.1 / §19 honesty). A second matrix would fork the truth.
- This layer defines META only. It does not fetch, persist, aggregate,
  interpret, or learn. `aggregation` is DECLARED here, applied elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from services import capability_registry

Unit        = Literal["rub", "percent", "units", "days", "rating", "count", "rank"]
Direction   = Literal["higher_better", "lower_better"]
Aggregation = Literal["sum", "weighted_avg", "min", "max"]
Grain       = Literal["listing", "product"]

# marketplace-code seam: map any caller code → capability_registry / adapter code.
# This is data mapping at the registry boundary, NOT business branching.
_MP_CODE: dict[str, str] = {
    "wb": "wb", "wildberries": "wb",
    "ozon": "ozon",
    "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex",
}


def normalize_marketplace(code: Optional[str]) -> Optional[str]:
    return _MP_CODE.get((code or "").lower())


@dataclass(frozen=True)
class MetricSpec:
    metric_name:    str
    capability_key: str          # FK into capability_registry — single source of availability
    unit:           Unit
    direction:      Direction
    grain:          Grain        # native read grain
    aggregation:    Aggregation  # listing → product roll-up rule (declared; applied elsewhere)
    semantic_def:   str          # canonical meaning adapters must satisfy or registry says impossible


_CATALOG: dict[str, MetricSpec] = {
    "revenue":          MetricSpec("revenue",          "sales",             "rub",     "higher_better", "listing", "sum",          "Выручка к перечислению за окно, ₽"),
    "units_sold":       MetricSpec("units_sold",       "sales",             "units",   "higher_better", "listing", "sum",          "Проданные единицы за окно"),
    "ctr":              MetricSpec("ctr",              "ad_metrics",        "percent", "higher_better", "listing", "weighted_avg", "Клики / показы за окно, %"),
    "impressions":      MetricSpec("impressions",      "ad_metrics",        "count",   "higher_better", "listing", "sum",          "Показы за окно"),
    "stock_units":      MetricSpec("stock_units",      "stocks",            "units",   "higher_better", "listing", "sum",          "Доступные остатки, шт"),
    "stock_days_cover": MetricSpec("stock_days_cover", "stocks",            "days",    "higher_better", "listing", "min",          "Дней обеспеченности остатком (worst listing governs)"),
    "rating":           MetricSpec("rating",           "product_rating",    "rating",  "higher_better", "listing", "weighted_avg", "Средняя оценка, 1–5"),
    "search_position":  MetricSpec("search_position",  "search_position",   "rank",    "lower_better",  "listing", "min",          "Позиция в поиске по ключу (меньше — лучше)"),
    "ad_cost_ratio":    MetricSpec("ad_cost_ratio",    "drr_profit_impact", "percent", "lower_better",  "listing", "weighted_avg", "ДРР: рекл. расход / выручка, %"),
    "net_profit":       MetricSpec("net_profit",       "net_profit",        "rub",     "higher_better", "listing", "sum",          "Чистая прибыль за окно, ₽ (compute из финансов)"),
}


def get(metric_name: str) -> Optional[MetricSpec]:
    return _CATALOG.get(metric_name)


def known_metrics() -> list[str]:
    return sorted(_CATALOG)


def availability(metric_name: str, marketplace: str, tariffs: Optional[set[str]] = None) -> dict:
    """
    Resolve whether `metric_name` is obtainable for `marketplace`, delegating
    entirely to capability_registry. Enriches the registry verdict with the
    metric's canonical semantics. Never fabricates availability.
    """
    spec = _CATALOG.get(metric_name)
    if spec is None:
        return {"available": False, "status": "unknown_metric", "metric_name": metric_name}

    mp = normalize_marketplace(marketplace)
    if mp is None:
        return {"available": False, "status": "no_adapter", "metric_name": metric_name,
                "reason": f"unknown marketplace: {marketplace}"}

    out = capability_registry.availability(spec.capability_key, mp, tariffs)
    out["metric_name"]   = metric_name
    out["unit"]          = spec.unit
    out["grain"]         = spec.grain
    out["direction"]     = spec.direction
    out["aggregation"]   = spec.aggregation
    out["capability_key"] = spec.capability_key
    return out
