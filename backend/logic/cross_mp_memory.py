"""
Cross-Marketplace Historical Memory — Sprint 28.

Builds institutional memory from resolved insight history.
Surfaces how similar systemic patterns were previously stabilized.
No AI language. Operational, restrained, factual.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class CrossMarketplaceMemory:
    pattern_type:            str
    marketplaces:            list[str]
    resolved_by:             str
    stability_duration_days: int
    recurrence:              bool
    resolved_at:             datetime


# Human-readable stabilization action per pattern type (genitive form for Russian sentences)
_RESOLVED_BY_DEFAULT: dict[str, str] = {
    "multi_margin_pressure":  "корректировки закупочной цены",
    "advertising_dependency": "оптимизации карточек",
    "stock_instability":      "коррекции цикла поставки",
    "price_pressure_cluster": "стабилизации цены",
    "seo_decay_cluster":      "пакетного пересмотра SEO",
}

# Insight key prefixes associated with each pattern type
_PATTERN_PREFIXES: dict[str, list[str]] = {
    "multi_margin_pressure":  ["margin_crisis", "high_ad_spend"],
    "advertising_dependency": ["high_ad_spend", "seo_opportunity"],
    "stock_instability":      ["low_stock"],
    "price_pressure_cluster": ["margin_crisis"],
    "seo_decay_cluster":      ["seo_opportunity"],
}


def build_cross_mp_memory(
    pattern_type:       str,
    resolved_history:   dict[str, datetime] | None = None,
    operator_decisions: list | None = None,
) -> Optional[CrossMarketplaceMemory]:
    """
    Build a CrossMarketplaceMemory from resolved insight history.

    resolved_history: dict[insight_key → resolved_at datetime]
    operator_decisions: reserved for future stabilization action matching.

    Returns None if no relevant history found.
    """
    _rh = resolved_history or {}
    relevant_prefixes = _PATTERN_PREFIXES.get(pattern_type, [])
    if not relevant_prefixes:
        return None

    matches: list[datetime] = [
        ts for key, ts in _rh.items()
        if any(key.startswith(pfx) for pfx in relevant_prefixes)
    ]
    if not matches:
        return None

    last_resolved = max(matches)
    now = datetime.utcnow()

    # Recurrence: multiple resolutions with gap > 7 days between any two
    recurrence = False
    stability_days = max(0, (now - last_resolved).days)

    if len(matches) >= 2:
        sorted_ts = sorted(matches)
        for i in range(1, len(sorted_ts)):
            gap = (sorted_ts[i] - sorted_ts[i - 1]).days
            if gap > 7:
                recurrence = True
                # stability_duration = how long it held between first resolution and recurrence
                stability_days = gap
                break

    resolved_by = _RESOLVED_BY_DEFAULT.get(pattern_type, "операционного вмешательства")

    return CrossMarketplaceMemory(
        pattern_type=pattern_type,
        marketplaces=[],
        resolved_by=resolved_by,
        stability_duration_days=stability_days,
        recurrence=recurrence,
        resolved_at=last_resolved,
    )


def build_memory_narrative(memory: Optional[CrossMarketplaceMemory]) -> Optional[str]:
    """
    Build a single display narrative from a CrossMarketplaceMemory.
    Returns None if memory is None (nothing to show).
    """
    if memory is None:
        return None

    if memory.recurrence:
        base = "Похожий паттерн ранее уже возвращался после временной стабилизации."
    else:
        base = (
            f"Похожее кросс-площадочное давление ранее было стабилизировано "
            f"после {memory.resolved_by}."
        )

    if memory.stability_duration_days > 60:
        base += f" Предыдущая стабилизация сохранялась {memory.stability_duration_days} дней."

    return base
