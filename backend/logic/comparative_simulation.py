"""
Comparative Simulation — Sprint 42.
Compares two operational paths per insight category.
Never produces a recommendation or ranking — only contextual operational comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ComparativePath:
    action_type:          str   # slug: reduce_ad_spend | campaign_restructure | etc.
    stabilization_speed:  str   # faster | moderate | slower
    volatility_impact:    str   # lower | moderate | higher
    observability_impact: str   # preserved | reduced | unclear
    operator_load:        str   # lower | moderate | higher
    reversibility_profile: str  # stronger | neutral | weaker
    structural_depth:     str   # tactical | mixed | structural
    path_note:            str   # restrained narrative for this path


@dataclass
class PathComparison:
    insight_key:          str
    path_a:               ComparativePath
    path_b:               ComparativePath
    contextual_note:      str   # restrained comparison narrative; no winner, no ranking
    comparison_dimension: str   # volatility | reversibility | speed | observability | load


# ── Registry ──────────────────────────────────────────────────────────────────
# Each entry: (path_a, path_b, base_contextual_note, comparison_dimension)

_REGISTRY: dict[str, tuple[ComparativePath, ComparativePath, str, str]] = {

    "high_ad_spend": (
        ComparativePath(
            action_type="reduce_ad_spend",
            stabilization_speed="faster",
            volatility_impact="higher",
            observability_impact="preserved",
            operator_load="lower",
            reversibility_profile="neutral",
            structural_depth="tactical",
            path_note="Снижение нагрузки обычно stabilizes быстрее, но может временно снизить revenue velocity.",
        ),
        ComparativePath(
            action_type="campaign_restructure",
            stabilization_speed="slower",
            volatility_impact="lower",
            observability_impact="preserved",
            operator_load="higher",
            reversibility_profile="stronger",
            structural_depth="mixed",
            path_note="Реструктуризация кампаний stabilizes медленнее, но чаще формирует более устойчивую рекламную структуру.",
        ),
        (
            "Снижение нагрузки обычно stabilizes быстрее, но может сопровождаться краткосрочной revenue softness. "
            "Реструктуризация кампаний stabilizes медленнее, но чаще формирует более устойчивую рекламную структуру."
        ),
        "volatility",
    ),

    "margin_crisis": (
        ComparativePath(
            action_type="increase_price",
            stabilization_speed="moderate",
            volatility_impact="higher",
            observability_impact="reduced",
            operator_load="moderate",
            reversibility_profile="neutral",
            structural_depth="tactical",
            path_note="Корректировка цены обычно stabilizes маржу быстрее, но может временно снизить конверсию.",
        ),
        ComparativePath(
            action_type="procurement_repricing",
            stabilization_speed="slower",
            volatility_impact="lower",
            observability_impact="preserved",
            operator_load="higher",
            reversibility_profile="stronger",
            structural_depth="structural",
            path_note="Repricing закупок stabilizes медленнее, но формирует более устойчивую margin structure.",
        ),
        (
            "Корректировка цены обычно stabilizes маржу быстрее, но может временно снизить конверсию. "
            "Repricing закупок stabilizes медленнее, но формирует более устойчивую margin structure."
        ),
        "reversibility",
    ),

    "seo_opportunity": (
        ComparativePath(
            action_type="rebuild_seo",
            stabilization_speed="faster",
            volatility_impact="higher",
            observability_impact="reduced",
            operator_load="lower",
            reversibility_profile="neutral",
            structural_depth="tactical",
            path_note="SEO-пересборка в текущем цикле может временно снизить observability результатов.",
        ),
        ComparativePath(
            action_type="optimize_keywords",
            stabilization_speed="slower",
            volatility_impact="lower",
            observability_impact="preserved",
            operator_load="lower",
            reversibility_profile="neutral",
            structural_depth="tactical",
            path_note="Оптимизация структуры ключевых слов обычно сохраняет более прозрачную attribution динамику.",
        ),
        (
            "SEO-пересборка в текущем цикле может временно снизить observability результатов. "
            "Оптимизация структуры ключевых слов обычно сохраняет более прозрачную attribution динамику."
        ),
        "observability",
    ),

    "sales_growth": (
        ComparativePath(
            action_type="increase_ads",
            stabilization_speed="faster",
            volatility_impact="higher",
            observability_impact="preserved",
            operator_load="moderate",
            reversibility_profile="neutral",
            structural_depth="tactical",
            path_note="Рост рекламной нагрузки stabilizes быстрее, но создаёт более высокую операционную volatility.",
        ),
        ComparativePath(
            action_type="stock_replenishment",
            stabilization_speed="slower",
            volatility_impact="lower",
            observability_impact="preserved",
            operator_load="moderate",
            reversibility_profile="stronger",
            structural_depth="structural",
            path_note="Пополнение стока stabilizes медленнее, но формирует более устойчивую growth structure.",
        ),
        (
            "Рост рекламной нагрузки stabilizes быстрее, но создаёт более высокую операционную volatility. "
            "Пополнение стока stabilizes медленнее, но формирует более устойчивую growth structure."
        ),
        "speed",
    ),
}

_CONTEXT_OVERLOAD = (
    " При текущей операционной нагрузке менее фрагментированный сценарий"
    " может быть operationally safer."
)
_CONTEXT_RECURRING = (
    " На текущем recurring давлении структурная стабилизация"
    " обычно формирует более устойчивый recovery path."
)
_CONTEXT_NARROWING = (
    " При сужающемся окне стабилизации более быстрый сценарий"
    " снижает риск перехода давления в следующую фазу."
)


def _extract_category(insight) -> str:
    key = getattr(insight, "key", "") or ""
    raw = key.split(":")[0]
    return raw.replace("demo_", "")


def compute_path_comparison(
    insight,
    capacity_state: str = "stable",
    lifecycle: Optional[str] = None,
    trajectory: Optional[str] = None,
    recovery_state: Optional[str] = None,
) -> Optional[PathComparison]:
    cat = _extract_category(insight)
    entry = _REGISTRY.get(cat)
    if entry is None:
        return None

    path_a, path_b, base_note, dimension = entry

    # ── Context-aware note adjustments ────────────────────────────────────────
    note = base_note
    if capacity_state in ("saturated", "overloaded"):
        if path_a.operator_load == "lower" or path_b.operator_load == "lower":
            note += _CONTEXT_OVERLOAD
    elif lifecycle == "recurring":
        has_structural = path_a.structural_depth == "structural" or path_b.structural_depth == "structural"
        if has_structural:
            note += _CONTEXT_RECURRING
    elif trajectory in ("escalating", "structurally_accumulating"):
        if path_a.stabilization_speed == "faster" or path_b.stabilization_speed == "faster":
            note += _CONTEXT_NARROWING

    return PathComparison(
        insight_key=getattr(insight, "key", cat),
        path_a=path_a,
        path_b=path_b,
        contextual_note=note,
        comparison_dimension=dimension,
    )
