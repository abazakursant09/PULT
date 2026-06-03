"""
Cross-Product Pattern Detection — Sprint 22.
Root Cause + Historical Memory enrichment — Sprint 28.

Detects operational patterns across multiple SKUs.
Not ML. Not anomaly detection. Operational grouping only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from logic.cross_mp_root_cause import infer_root_cause, confidence_band as _rc_band
from logic.cross_mp_memory import build_cross_mp_memory, build_memory_narrative


@dataclass
class PortfolioPattern:
    id:                      str
    pattern_type:            str
    marketplace:             str | None
    category:                str | None
    affected_products:       list[str]
    insight_types:           list[str]
    operational_summary:     str
    systemic_risk:           str
    confidence:              int
    stabilization_complexity: Literal["localized", "moderate", "systemic"]
    recommendation_bias:     str | None = None
    # Sprint 28: root cause hypothesis (confidence independent of signal confidence)
    root_cause_hypothesis:   Optional[str] = None
    root_cause_note:         Optional[str] = None
    root_cause_confidence:   Optional[int] = None
    root_cause_band:         Optional[str] = None
    # Sprint 28: historical memory
    cross_mp_memory_note:    Optional[str] = None
    cross_mp_stability_days: Optional[int] = None


# ── Normalization helpers ──────────────────────────────────────────────────────

def _cat(key: str) -> str:
    raw = key.split(":")[0]
    return raw[len("demo_"):] if raw.startswith("demo_") else raw


def _mp_label(mp: str | None) -> str:
    return {
        "wildberries":   "WB",
        "ozon":          "Ozon",
        "yandex_market": "Яндекс Маркет",
    }.get(mp or "", mp or "")


# ── Detectors ──────────────────────────────────────────────────────────────────

def detect_multi_margin_pressure(active: list[dict]) -> PortfolioPattern | None:
    """
    ≥2 margin_crisis insights OR 1 margin_crisis + 1 high_ad_spend on same marketplace.
    Signals that margin instability is not localized to one SKU.
    """
    mc = [i for i in active if _cat(i["key"]) == "margin_crisis"]
    ha = [i for i in active if _cat(i["key"]) == "high_ad_spend"]

    # Case A: 2+ distinct margin crisis events
    if len(mc) >= 2:
        mps = {i["marketplace"] for i in mc}
        mp  = next(iter(mps)) if len(mps) == 1 else None
        products = list({i["product_name"] for i in mc if i["product_name"]})
        conf = min(i["confidence"] for i in mc)
        return PortfolioPattern(
            id=f"pp-{uuid.uuid4().hex[:8]}",
            pattern_type="multi_margin_pressure",
            marketplace=mp,
            category=None,
            affected_products=products,
            insight_types=["margin_crisis"],
            operational_summary=(
                f"Несколько товаров одновременно вышли за устойчивый диапазон маржи. "
                f"Давление распределено по {len(products)} SKU."
            ),
            systemic_risk="Структурное давление на маржу не локализовано — возможна категорийная динамика.",
            confidence=min(82, conf + 5),
            stabilization_complexity="systemic",
            recommendation_bias="audit_category_economics",
        )

    # Case B: 1 margin_crisis + 1 high_ad_spend on same marketplace
    if mc and ha:
        mc0, ha0 = mc[0], ha[0]
        if mc0["marketplace"] == ha0["marketplace"]:
            products = list({i["product_name"] for i in [mc0, ha0] if i["product_name"]})
            return PortfolioPattern(
                id=f"pp-{uuid.uuid4().hex[:8]}",
                pattern_type="multi_margin_pressure",
                marketplace=mc0["marketplace"],
                category=None,
                affected_products=products,
                insight_types=["margin_crisis", "high_ad_spend"],
                operational_summary=(
                    "Рекламная нагрузка и давление на маржу одновременно наблюдаются "
                    "в нескольких SKU на одной площадке."
                ),
                systemic_risk="Рост ДРР опережает выручку не в одном товаре, а на уровне площадки.",
                confidence=78,
                stabilization_complexity="systemic",
                recommendation_bias="reduce_platform_ad_dependency",
            )

    return None


def detect_advertising_dependency(active: list[dict]) -> PortfolioPattern | None:
    """
    high_ad_spend + seo_opportunity on same marketplace.
    Ads spending but organic CTR not converting.
    """
    ha  = [i for i in active if _cat(i["key"]) == "high_ad_spend"]
    seo = [i for i in active if _cat(i["key"]) == "seo_opportunity"]
    if not ha or not seo:
        return None

    # At least one pair on same marketplace
    for h in ha:
        for s in seo:
            if h["marketplace"] == s["marketplace"]:
                mp       = h["marketplace"]
                products = list({i["product_name"] for i in [h, s] if i["product_name"]})
                conf     = min(h["confidence"], s["confidence"])
                return PortfolioPattern(
                    id=f"pp-{uuid.uuid4().hex[:8]}",
                    pattern_type="advertising_dependency",
                    marketplace=mp,
                    category=None,
                    affected_products=products,
                    insight_types=["high_ad_spend", "seo_opportunity"],
                    operational_summary=(
                        f"Рост рекламной нагрузки перестал сопровождаться органическим CTR. "
                        f"Карточки теряют конверсию при активной рекламе."
                    ),
                    systemic_risk=(
                        "Реклама компенсирует слабость карточек, а не усиливает органику. "
                        "Без SEO-коррекции нагрузка продолжит расти."
                    ),
                    confidence=min(80, conf + 3),
                    stabilization_complexity="moderate",
                    recommendation_bias="fix_cards_before_scaling_ads",
                )
    return None


def detect_seo_decay_cluster(active: list[dict]) -> PortfolioPattern | None:
    """≥2 seo_opportunity signals on same marketplace."""
    seo = [i for i in active if _cat(i["key"]) == "seo_opportunity"]
    if len(seo) < 2:
        return None
    mps      = {i["marketplace"] for i in seo}
    mp       = next(iter(mps)) if len(mps) == 1 else None
    products = list({i["product_name"] for i in seo if i["product_name"]})
    conf     = min(i["confidence"] for i in seo)
    return PortfolioPattern(
        id=f"pp-{uuid.uuid4().hex[:8]}",
        pattern_type="seo_decay_cluster",
        marketplace=mp,
        category=None,
        affected_products=products,
        insight_types=["seo_opportunity"],
        operational_summary=(
            f"Карточки {len(products)} товаров постепенно теряют органическую устойчивость. "
            f"Снижение CTR наблюдается одновременно."
        ),
        systemic_risk="Категорийная конкуренция или алгоритмический сдвиг площадки — не локальный дефект карточки.",
        confidence=min(78, conf + 4),
        stabilization_complexity="moderate",
        recommendation_bias="batch_seo_rebuild",
    )


def detect_stock_instability(active: list[dict]) -> PortfolioPattern | None:
    """≥2 low_stock signals — operational supply cycle failing."""
    ls = [i for i in active if _cat(i["key"]) == "low_stock"]
    if len(ls) < 2:
        return None
    mps      = {i["marketplace"] for i in ls}
    mp       = next(iter(mps)) if len(mps) == 1 else None
    products = list({i["product_name"] for i in ls if i["product_name"]})
    return PortfolioPattern(
        id=f"pp-{uuid.uuid4().hex[:8]}",
        pattern_type="stock_instability",
        marketplace=mp,
        category=None,
        affected_products=products,
        insight_types=["low_stock"],
        operational_summary=(
            f"Операционный цикл поставки перестал компенсировать скорость продаж "
            f"для {len(products)} товаров одновременно."
        ),
        systemic_risk="Одновременный out-of-stock нескольких SKU усиливает потерю органических позиций.",
        confidence=85,
        stabilization_complexity="moderate",
        recommendation_bias="review_replenishment_cycle",
    )


def detect_ozon_attribution_noise(active: list[dict]) -> PortfolioPattern | None:
    """Sales growth on Ozon with delayed automation — attribution window may distort signal."""
    ozon_growth = [
        i for i in active
        if _cat(i["key"]) == "sales_growth"
        and (i.get("marketplace") or "").lower() == "ozon"
        and i.get("automation_level") in ("delayed", None)
    ]
    if not ozon_growth:
        return None
    products = list({i["product_name"] for i in ozon_growth if i["product_name"]})
    return PortfolioPattern(
        id=f"pp-{uuid.uuid4().hex[:8]}",
        pattern_type="ozon_attribution_noise",
        marketplace="ozon",
        category=None,
        affected_products=products,
        insight_types=["sales_growth"],
        operational_summary=(
            "Часть сигналов роста на Ozon может быть связана с задержкой атрибуции. "
            "Данные стабилизируются через 24–48ч."
        ),
        systemic_risk="Решения на основе незрелых данных Ozon могут переоценить устойчивость роста.",
        confidence=72,
        stabilization_complexity="localized",
        recommendation_bias=None,
    )


def detect_price_pressure_cluster(active: list[dict]) -> PortfolioPattern | None:
    """
    ≥2 margin_crisis on same marketplace (different products).
    Signals category-level price competition.
    """
    mc = [i for i in active if _cat(i["key"]) == "margin_crisis"]
    # Must be different products
    unique_prods = {i["product_name"] for i in mc if i["product_name"]}
    if len(unique_prods) < 2:
        return None
    mps = {i["marketplace"] for i in mc}
    mp  = next(iter(mps)) if len(mps) == 1 else None
    return PortfolioPattern(
        id=f"pp-{uuid.uuid4().hex[:8]}",
        pattern_type="price_pressure_cluster",
        marketplace=mp,
        category=None,
        affected_products=list(unique_prods),
        insight_types=["margin_crisis"],
        operational_summary=(
            "Снижение маржи одновременно фиксируется у нескольких товаров категории. "
            "Возможно категорийное ценовое давление."
        ),
        systemic_risk="Изменения цены могут усилить давление, если категория в фазе ценовой войны.",
        confidence=76,
        stabilization_complexity="systemic",
        recommendation_bias="stabilize_price_first",
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def detect_portfolio_patterns(
    insight_summaries: list[dict],
    max_patterns: int = 5,
    resolved_history: dict[str, datetime] | None = None,
    insights_raw: list | None = None,
) -> list[PortfolioPattern]:
    """
    Detect cross-product operational patterns from a list of insight summaries.

    Each dict must have: key, marketplace, product_name, product_sku,
                         confidence, status, automation_level (optional).

    resolved_history: optional dict[insight_key → resolved_at] for Sprint 28 enrichment.
    insights_raw: optional list of InsightItem objects for lifecycle-aware root cause scoring.

    Returns at most max_patterns patterns, prioritized by stabilization_complexity.
    """
    active = [
        i for i in insight_summaries
        if i.get("status") not in ("resolved", "dismissed")
    ]

    if len(active) < 2:
        return []

    detectors = [
        detect_multi_margin_pressure,
        detect_advertising_dependency,
        detect_seo_decay_cluster,
        detect_stock_instability,
        detect_ozon_attribution_noise,
        detect_price_pressure_cluster,
    ]

    results: list[PortfolioPattern] = []
    seen_types: set[str] = set()

    for detector in detectors:
        p = detector(active)
        if p and p.pattern_type not in seen_types:
            results.append(p)
            seen_types.add(p.pattern_type)

    # Sort: systemic first, then moderate, then localized; higher confidence first
    _complexity_order = {"systemic": 0, "moderate": 1, "localized": 2}
    results.sort(key=lambda p: (_complexity_order.get(p.stabilization_complexity, 3), -p.confidence))
    results = results[:max_patterns]

    # Sprint 28: enrich with root cause hypothesis and historical memory
    _rh = resolved_history or {}
    _ins = insights_raw or []
    for p in results:
        rc = infer_root_cause(p, _ins, _rh)
        if rc is not None:
            p.root_cause_hypothesis = rc.hypothesis
            p.root_cause_note       = rc.operational_note
            p.root_cause_confidence = rc.confidence
            p.root_cause_band       = _rc_band(rc.confidence)

        mem = build_cross_mp_memory(p.pattern_type, _rh)
        narrative = build_memory_narrative(mem)
        if narrative:
            p.cross_mp_memory_note    = narrative
            p.cross_mp_stability_days = mem.stability_duration_days if mem else None

    return results


def insight_to_summary(insight) -> dict:
    """Convert InsightItem or similar object to the dict format detect_portfolio_patterns expects."""
    return {
        "key":             getattr(insight, "key", ""),
        "marketplace":     getattr(insight, "marketplace", None),
        "product_name":    getattr(insight, "product_name", None),
        "product_sku":     getattr(insight, "product_sku", None),
        "confidence":      getattr(insight, "confidence", 0),
        "status":          getattr(insight, "status", "active"),
        "automation_level": getattr(insight, "automation_level", None),
    }
