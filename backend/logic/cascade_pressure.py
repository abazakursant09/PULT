"""
Secondary Pressure Propagation / Cascade Intelligence — Sprint 50.
Detects when stabilization in one zone creates secondary pressure in adjacent operational zones.
Never frames as 'intervention was wrong'. Only: operational coupling, pressure migration, secondary observability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Category registry: secondary_pressure_target per insight key
_CASCADE_TARGETS: dict[str, str] = {
    "margin_crisis":    "ценовое позиционирование",
    "high_ad_spend":    "unit-экономику",
    "low_stock":        "операционный буфер",
    "seo_opportunity":  "конверсионную воронку",
    "high_rating":      "объём обслуживания",
    "sales_growth":     "маржинальную модель",
}

# Category + state → cascade_note
_CASCADE_NOTES: dict[tuple[str, str], str] = {
    ("margin_crisis", "shifting_pressure"):
        "Структурная коррекция маржи может постепенно переместить давление в область объёма продаж.",
    ("margin_crisis", "coupled_instability"):
        "Нестабильность в unit-экономике создаёт связанное операционное давление на смежные категории.",
    ("margin_crisis", "structurally_cascading"):
        "Системная нестабильность маржи начинает влиять на несколько операционных зон одновременно.",
    ("high_ad_spend", "shifting_pressure"):
        "Коррекция рекламного бюджета вероятно затронет показатели органической видимости.",
    ("high_ad_spend", "coupled_instability"):
        "Рекламная нагрузка оказывает связанное давление на конверсию и unit-экономику.",
    ("high_ad_spend", "structurally_cascading"):
        "Системная рекламная нагрузка начинает создавать давление одновременно в нескольких операционных зонах.",
    ("low_stock", "shifting_pressure"):
        "Балансировка складских запасов может постепенно создавать давление на прогнозирование спроса.",
    ("low_stock", "coupled_instability"):
        "Нестабильность стока создаёт связанное давление на операционный буфер и рекламную эффективность.",
    ("low_stock", "structurally_cascading"):
        "Системный дефицит стока начинает влиять на позиционирование и операционный буфер.",
    ("seo_opportunity", "shifting_pressure"):
        "SEO-изменения могут постепенно оказать давление на рекламную нагрузку в смежных категориях.",
    ("seo_opportunity", "coupled_instability"):
        "Операционная зависимость SEO и рекламы создаёт связанную нестабильность.",
    ("seo_opportunity", "structurally_cascading"):
        "Системная SEO-нестабильность затрагивает конверсионную модель и рекламный баланс.",
    ("high_rating", "shifting_pressure"):
        "Изменения в рейтинговой динамике могут создать давление на объём обслуживания.",
    ("high_rating", "coupled_instability"):
        "Нестабильность рейтинга создаёт связанное давление на операционный приоритет.",
    ("high_rating", "structurally_cascading"):
        "Системная рейтинговая нестабильность затрагивает несколько операционных зон.",
    ("sales_growth", "shifting_pressure"):
        "Рост продаж может перемещать операционное давление в область маржинальной устойчивости.",
    ("sales_growth", "coupled_instability"):
        "Быстрый рост создаёт связанную нагрузку на маржинальную модель и операционный буфер.",
    ("sales_growth", "structurally_cascading"):
        "Системный рост начинает создавать давление одновременно на маржу, сток и операционный буфер.",
}

_DEFAULT_CASCADE_NOTES: dict[str, str] = {
    "shifting_pressure":     "Операционное давление начинает постепенно перемещаться в смежные зоны стабилизации.",
    "coupled_instability":   "Нестабильность формирует связанное давление в нескольких операционных зонах.",
    "structurally_cascading": "Системное давление охватывает несколько операционных зон.",
}

_CASCADE_WINDOWS: dict[str, Optional[int]] = {
    "isolated":              None,
    "shifting_pressure":     14,
    "coupled_instability":   10,
    "structurally_cascading": 7,
}

# State → cascade_direction
_CASCADE_DIRECTIONS: dict[str, str] = {
    "isolated":              "localized",
    "shifting_pressure":     "adjacent",
    "coupled_instability":   "expanding",
    "structurally_cascading": "systemic",
}

# State → focus weight delta (applied by _enrich_cascade)
CASCADE_WEIGHT_DELTA: dict[str, int] = {
    "isolated":              0,
    "shifting_pressure":     3,
    "coupled_instability":   8,
    "structurally_cascading": 14,
}


@dataclass
class CascadePressure:
    cascade_state:             str
    cascade_direction:         str
    secondary_pressure_target: Optional[str]
    cascade_probability:       int
    cascade_window_days:       Optional[int]
    cascade_note:              Optional[str]
    cascade_offset_note:       Optional[str]


def compute_cascade_pressure(
    insight_category:             str,
    trajectory_state:             Optional[str],
    trajectory_direction:         Optional[str],
    reversal_state:               Optional[str],
    reversal_probability:         Optional[int],
    counterfactual_pressure_state: Optional[str],
    pressure_accumulation:        Optional[str],
    tradeoff_severity:            Optional[str],
    signal_lifecycle_stage:       Optional[str],
    signal_recurrence_count:      Optional[int],
    stabilization_window_days:    Optional[int],
    timing_state:                 Optional[str],
) -> CascadePressure:
    """
    Classify secondary pressure propagation for a single insight.
    State priority: structurally_cascading > coupled_instability > shifting_pressure > isolated.
    """
    rec_count = signal_recurrence_count or 0
    rev_prob  = reversal_probability or 0

    # Build propagation probability
    base = 15
    if trajectory_direction in ("worsening", "critical"):
        base += 12
    if trajectory_state in ("escalating", "structurally_accumulating"):
        base += 10
    if counterfactual_pressure_state in ("compounding", "narrowing_window", "structurally_expensive"):
        base += 12
    if pressure_accumulation in ("accumulating", "compounding"):
        base += 8
    if pressure_accumulation == "compounding":
        base += 4
    if reversal_state in ("overextended", "structurally_locked"):
        base += 15
    elif reversal_state == "reversal_window":
        base += 5
    if tradeoff_severity in ("moderate", "significant"):
        base += 6
    if tradeoff_severity == "significant":
        base += 4
    if signal_lifecycle_stage in ("recurring", "confirmed", "persistent"):
        base += 5 + min(rec_count * 2, 8)
    if timing_state in ("narrowing_window", "structurally_late", "immediate"):
        base += 8
    if stabilization_window_days is not None and stabilization_window_days <= 7:
        base += 5

    cascade_prob = min(base, 92)

    # Classify state
    is_recurring      = signal_lifecycle_stage in ("recurring", "confirmed", "persistent")
    is_escalating     = (
        trajectory_direction in ("worsening", "critical")
        or trajectory_state in ("escalating", "structurally_accumulating")
    )
    is_cf_high        = counterfactual_pressure_state in ("narrowing_window", "structurally_expensive")
    is_reversal_active = reversal_state in ("overextended", "structurally_locked")
    is_pressure_high  = pressure_accumulation in ("accumulating", "compounding")
    is_timing_critical = timing_state in ("narrowing_window", "structurally_late", "immediate")

    if (
        is_recurring and is_escalating and is_cf_high
        and is_reversal_active and is_pressure_high
    ):
        cascade_state = "structurally_cascading"
    elif (
        (is_reversal_active and is_recurring and is_escalating)
        or (is_cf_high and is_escalating and is_recurring and is_pressure_high)
    ):
        cascade_state = "coupled_instability"
    elif (
        (is_escalating and is_recurring and (is_timing_critical or is_cf_high))
        or (is_reversal_active and not is_recurring)
        or (is_pressure_high and is_escalating)
    ):
        cascade_state = "shifting_pressure"
    else:
        cascade_state = "isolated"

    cascade_direction = _CASCADE_DIRECTIONS[cascade_state]
    window_days       = _CASCADE_WINDOWS[cascade_state]
    target            = _CASCADE_TARGETS.get(insight_category)

    note: Optional[str] = None
    if cascade_state != "isolated":
        note = _CASCADE_NOTES.get((insight_category, cascade_state))
        if note is None:
            note = _DEFAULT_CASCADE_NOTES.get(cascade_state)

    offset_note: Optional[str] = None
    if cascade_state == "structurally_cascading":
        offset_note = "Вторичное давление может проявиться одновременно в нескольких операционных зонах."
    elif cascade_state == "coupled_instability" and window_days:
        offset_note = f"Смещение давления вероятно начнёт проявляться в течение ≈{window_days} дней."
    elif cascade_state == "shifting_pressure" and window_days:
        offset_note = f"Операционная миграция давления ожидается в горизонте ≈{window_days} дней."

    return CascadePressure(
        cascade_state=cascade_state,
        cascade_direction=cascade_direction,
        secondary_pressure_target=target,
        cascade_probability=cascade_prob,
        cascade_window_days=window_days,
        cascade_note=note,
        cascade_offset_note=offset_note,
    )
