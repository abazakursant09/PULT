"""
Cross-Marketplace Root Cause Engine — Sprint 28.
Infers probable operational root causes from portfolio patterns.
Not AI. Not prediction. Operational pattern matching + institutional knowledge.
Confidence is computed independently — never inherits signal confidence.
"""
from __future__ import annotations

import dataclasses
from typing import Literal, Optional, Any

RootCauseConfidenceBand = Literal["low", "moderate", "stable", "high"]

# Known hypotheses per pattern type — listed by frequency (lead hypothesis first)
ROOT_CAUSE_PATTERNS: dict[str, list[str]] = {
    "multi_margin_pressure": [
        "Закупочная цена",
        "Структура unit-экономики",
        "Логистическая нагрузка",
        "Рекламная dependency",
    ],
    "advertising_dependency": [
        "Слабая органическая конверсия",
        "Низкая эффективность карточки",
        "Overbidding в рекламе",
    ],
    "stock_instability": [
        "Недостаточный порог пополнения",
        "Слабый forecasting",
        "Разрыв supply rhythm",
    ],
    "seo_decay_cluster": [
        "Алгоритмический сдвиг площадки",
        "Усиление категорийной конкуренции",
        "Устаревший контент карточек",
    ],
    "price_pressure_cluster": [
        "Ценовая война в категории",
        "Снижение рыночной цены конкурентами",
        "Структурное давление на маржу категории",
    ],
}

_OPERATIONAL_NOTES: dict[str, str] = {
    "multi_margin_pressure": (
        "Кросс-площадочное давление чаще связано со структурой затрат или закупочной ценой, "
        "чем с механикой отдельной площадки."
    ),
    "advertising_dependency": (
        "Одновременная зависимость от рекламы на нескольких площадках обычно указывает "
        "на слабую органическую конверсию карточки."
    ),
    "stock_instability": (
        "Повторяющиеся проблемы остатков между площадками часто связаны "
        "с forecasting или supply rhythm."
    ),
    "seo_decay_cluster": (
        "Одновременное снижение CTR в нескольких карточках может указывать "
        "на алгоритмический сдвиг или усиление конкуренции в категории."
    ),
    "price_pressure_cluster": (
        "Параллельное давление на маржу в категории чаще связано с ценовой динамикой рынка, "
        "чем с внутренними операционными решениями."
    ),
}

# Base root cause confidence — deliberately conservative, decoupled from signal confidence
_BASE_CONFIDENCE: dict[str, int] = {
    "multi_margin_pressure":  52,
    "advertising_dependency": 58,
    "stock_instability":      61,
    "seo_decay_cluster":      49,
    "price_pressure_cluster": 47,
}


@dataclasses.dataclass
class RootCauseHypothesis:
    pattern_type:     str
    hypothesis:       str
    confidence:       int
    confidence_band:  RootCauseConfidenceBand
    operational_note: str


def _band(confidence: int) -> RootCauseConfidenceBand:
    if confidence >= 75: return "high"
    if confidence >= 60: return "stable"
    if confidence >= 40: return "moderate"
    return "low"


def infer_root_cause(
    portfolio_pattern: Any,
    insights:          list[Any],
    resolved_history:  dict,
) -> RootCauseHypothesis | None:
    """
    Returns the most probable root cause hypothesis for a portfolio pattern.
    Confidence is computed from pattern breadth and recurrence evidence — never from signal confidence.
    Returns None for patterns without root cause inference (e.g., attribution noise).
    """
    ptype = getattr(portfolio_pattern, "pattern_type", "")
    if ptype not in ROOT_CAUSE_PATTERNS:
        return None

    base_conf = _BASE_CONFIDENCE.get(ptype, 0)
    if base_conf == 0:
        return None

    hypotheses = ROOT_CAUSE_PATTERNS[ptype]
    conf = base_conf

    # Broader affected scope → slightly stronger evidence
    affected = getattr(portfolio_pattern, "affected_products", [])
    if len(affected) >= 3:
        conf += 6
    if len(affected) >= 5:
        conf += 4

    # Recurring lifecycle on any insight → pattern confirmed by repetition
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) == "recurring":
            conf += 5
            break

    # Prior resolution of a relevant insight type → pattern is recognized
    for insight_type in getattr(portfolio_pattern, "insight_types", []):
        if resolved_history.get(insight_type):
            conf += 4
            break

    # systemic complexity adds evidence weight
    if getattr(portfolio_pattern, "stabilization_complexity", "") == "systemic":
        conf += 3

    conf = min(conf, 78)  # cap: root cause never claims certainty

    return RootCauseHypothesis(
        pattern_type=ptype,
        hypothesis=hypotheses[0],
        confidence=conf,
        confidence_band=_band(conf),
        operational_note=_OPERATIONAL_NOTES.get(ptype, ""),
    )


# Public alias used by portfolio_patterns.py
confidence_band = _band
