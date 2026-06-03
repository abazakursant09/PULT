"""
Outcome Feedback Loop — Sprint 26.
Evaluates whether prior operational interventions were effective.
No ML. Pure evidence accumulation from outcome_state + lifecycle + recurrence.
"""
from __future__ import annotations

import dataclasses
from typing import Literal, Optional

OutcomeResult      = Literal["improved", "stabilized", "temporary", "failed", "unknown"]
RecommendationBias = Literal["reinforce", "neutral", "deprioritize"]

# Half-life mirrors signal_lifecycle.py
_HALF_LIFE: dict[str, int] = {
    "seo_opportunity": 18,
    "high_ad_spend":   11,
    "margin_crisis":   45,
    "low_stock":        7,
    "high_rating":     30,
    "sales_growth":    21,
}
_DEFAULT_HALF_LIFE = 14

_REINFORCE: dict[str, str] = {
    "seo_opportunity": "SEO-пересборка ранее восстанавливала CTR этого товара.",
    "high_ad_spend":   "Снижение рекламных ставок ранее приводило к устойчивой стабилизации ДРР.",
    "margin_crisis":   "Корректировка структуры затрат ранее восстанавливала маржинальность.",
    "low_stock":       "Своевременное пополнение склада ранее предотвращало потери продаж.",
    "high_rating":     "Работа с отзывами ранее стабилизировала рейтинг.",
    "sales_growth":    "Масштабирование рекламы ранее усиливало органический рост.",
}
_REINFORCE_DEFAULT = "Изменение ранее приводило к устойчивой стабилизации."

_NEUTRAL: dict[str, str] = {
    "seo_opportunity": "SEO-изменения ранее сопровождались временным улучшением CTR.",
    "high_ad_spend":   "Снижение рекламной нагрузки ранее сопровождалось временным улучшением.",
    "margin_crisis":   "Корректировки ранее давали временный эффект без устойчивой стабилизации.",
    "low_stock":       "Пополнение склада ранее временно устраняло дефицит.",
    "high_rating":     "Ответы на отзывы ранее давали краткосрочный эффект.",
    "sales_growth":    "Рост ранее сопровождался временным усилением, не подтверждённым устойчиво.",
}
_NEUTRAL_DEFAULT = "Ранее сопровождалось временным улучшением."

_DEPRIORITIZE: dict[str, str] = {
    "seo_opportunity": "Предыдущие пересборки карточки не дали устойчивого эффекта на CTR.",
    "high_ad_spend":   "Предыдущие изменения ставок не устранили структурное давление.",
    "margin_crisis":   "Предыдущие изменения не устранили структурное давление на маржу.",
    "low_stock":       "Пополнения склада ранее не предотвращали повторный дефицит.",
    "high_rating":     "Стандартные ответы на отзывы ранее не улучшали оценку устойчиво.",
    "sales_growth":    "Масштабирование ранее не закрепляло рост в долгосрочной перспективе.",
}
_DEPRIORITIZE_DEFAULT = "Похожее действие ранее не привело к устойчивому снижению давления."

# Alternative first recommendation injected when bias is deprioritize
_ALT_RECS: dict[str, str] = {
    "seo_opportunity": "Проверить ценовое позиционирование — предыдущие пересборки не дали устойчивого эффекта на CTR.",
    "high_ad_spend":   "Рассмотреть структурный аудит кампаний — снижение ставок ранее не устраняло давление устойчиво.",
    "margin_crisis":   "Проверить ценовое позиционирование — предыдущие изменения не устранили структурное давление на маржу.",
    "low_stock":       "Пересмотреть логистическую цепочку — пополнения склада ранее не предотвращали дефицит.",
    "high_rating":     "Провести глубокий анализ отзывов — стандартные ответы ранее не улучшали оценку устойчиво.",
    "sales_growth":    "Изучить структуру конверсии — масштабирование ранее не закрепляло рост.",
}


@dataclasses.dataclass
class OutcomeFeedback:
    action_type:           str
    outcome:               OutcomeResult
    effect_duration_days:  Optional[int]
    recurrence_after_days: Optional[int]
    confidence_delta:      int            # +10 reinforce | -6 neutral | -12 deprioritize | 0 unknown
    recommendation_bias:   RecommendationBias
    narrative:             str
    alt_recommendation:    Optional[str]  # replacement lead rec for deprioritize cases


def evaluate_operator_action(
    *,
    insight_type:     str,
    action_taken:     str,         # accepted | ignored | dismissed_again
    outcome_state:    str | None,  # improved | stabilized | temporary | failed | repeated
    lifecycle_stage:  str | None,  # emerging | confirmed | stabilized | recurring | resolved
    recurrence_count: int,
    notif_count:      int,
) -> OutcomeFeedback:
    """
    Returns evidence-backed recommendation bias for this signal type.
    Decision tree: successful → temporary → failed → unknown.
    No ML, no model training — purely operational evidence from recorded outcomes.
    """
    half_life = _HALF_LIFE.get(insight_type, _DEFAULT_HALF_LIFE)

    # A. SUCCESSFUL INTERVENTION
    if outcome_state in ("improved", "stabilized") and lifecycle_stage != "recurring":
        return OutcomeFeedback(
            action_type=insight_type,
            outcome="stabilized",
            effect_duration_days=half_life,
            recurrence_after_days=None,
            confidence_delta=+10,
            recommendation_bias="reinforce",
            narrative=_REINFORCE.get(insight_type, _REINFORCE_DEFAULT),
            alt_recommendation=None,
        )

    # B. TEMPORARY FIX
    if outcome_state == "temporary" or (lifecycle_stage == "recurring" and recurrence_count == 1):
        return OutcomeFeedback(
            action_type=insight_type,
            outcome="temporary",
            effect_duration_days=max(1, half_life // 3),
            recurrence_after_days=half_life // 2,
            confidence_delta=-6,
            recommendation_bias="neutral",
            narrative=_NEUTRAL.get(insight_type, _NEUTRAL_DEFAULT),
            alt_recommendation=None,
        )

    # C. FAILED INTERVENTION
    if outcome_state in ("repeated", "failed") or (lifecycle_stage == "recurring" and recurrence_count >= 2):
        return OutcomeFeedback(
            action_type=insight_type,
            outcome="failed",
            effect_duration_days=None,
            recurrence_after_days=max(1, half_life // 4) * max(notif_count, 1),
            confidence_delta=-12,
            recommendation_bias="deprioritize",
            narrative=_DEPRIORITIZE.get(insight_type, _DEPRIORITIZE_DEFAULT),
            alt_recommendation=_ALT_RECS.get(insight_type),
        )

    # D. UNKNOWN — insufficient history
    return OutcomeFeedback(
        action_type=insight_type,
        outcome="unknown",
        effect_duration_days=None,
        recurrence_after_days=None,
        confidence_delta=0,
        recommendation_bias="neutral",
        narrative="Исторических данных недостаточно для оценки эффекта.",
        alt_recommendation=None,
    )
