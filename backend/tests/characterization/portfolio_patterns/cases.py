"""Characterization fixtures for logic.portfolio_patterns (Sprint 71). Observe-only.

Hand-built: detectors consume summary DICTS (key/marketplace/product_name/...),
not insight objects. PortfolioPattern.id is a random uuid -> sanitized to "<id>"
so the snapshot is deterministic (we freeze behavior, not random identifiers).
"""
from logic.portfolio_patterns import (
    detect_advertising_dependency, detect_multi_margin_pressure,
    detect_ozon_attribution_noise, detect_portfolio_patterns,
    detect_price_pressure_cluster, detect_seo_decay_cluster,
    detect_stock_instability, insight_to_summary,
)
from characterization._engine import call, insight, jsonable


def _summary(key, product, mp="wildberries", conf=80, status="active"):
    return {"key": key, "marketplace": mp, "product_name": product,
            "product_sku": product.lower(), "confidence": conf, "status": status,
            "automation_level": None}


_SUMMARIES = [
    _summary("margin_crisis:wildberries:A", "AlphaMug"),
    _summary("margin_crisis:wildberries:B", "BetaCup"),
    _summary("high_ad_spend:wildberries:A", "AlphaMug"),
]


def _sanitize(value):
    """Replace random uuid 'id' fields so the snapshot is deterministic."""
    if isinstance(value, dict):
        return {k: ("<id>" if k == "id" else _sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def build_cases():
    c = {}
    for name, fn in [
        ("detect_multi_margin_pressure", detect_multi_margin_pressure),
        ("detect_advertising_dependency", detect_advertising_dependency),
        ("detect_seo_decay_cluster", detect_seo_decay_cluster),
        ("detect_stock_instability", detect_stock_instability),
        ("detect_ozon_attribution_noise", detect_ozon_attribution_noise),
        ("detect_price_pressure_cluster", detect_price_pressure_cluster),
    ]:
        c[name] = _sanitize(call(fn, _SUMMARIES))
        c[f"{name}.empty"] = _sanitize(call(fn, []))

    c["detect_portfolio_patterns"] = _sanitize(call(detect_portfolio_patterns, _SUMMARIES))
    c["detect_portfolio_patterns.empty"] = _sanitize(call(detect_portfolio_patterns, []))
    c["insight_to_summary"] = call(insight_to_summary, insight(product_name="AlphaMug", product_sku="a"))
    return c
