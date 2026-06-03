"""
Retrospective Outcome Memory — Sprint 21.

Evaluates what happened after past interventions.
Not predictions. Not scoring. Operational hindsight only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


@dataclass
class OutcomeEvaluation:
    insight_type:           str
    intervention:           str
    outcome:                Literal["improved", "stabilized", "temporary", "failed", "repeated"]
    evaluation_window_days: int
    metric_delta:           float | None
    explanation:            str
    confidence:             int
    recurrence_detected:    bool


# ── Decision half-life: how long effect typically holds per category ────────────

RESOLUTION_HALF_LIFE: dict[str, int] = {
    "seo_opportunity": 18,
    "high_ad_spend":   11,
    "margin_crisis":   45,
    "low_stock":        7,
    "high_rating":     30,
    "sales_growth":    21,
}

TYPICAL_INTERVENTION: dict[str, str] = {
    "seo_opportunity": "SEO-пересборка карточки",
    "high_ad_spend":   "корректировка рекламных ставок",
    "margin_crisis":   "оптимизация структуры затрат",
    "low_stock":       "пополнение склада",
    "high_rating":     "использование рейтинга в рекламе",
    "sales_growth":    "масштабирование рекламного бюджета",
}

# Human-readable outcome notes by category + outcome
_OUTCOME_TEMPLATES: dict[str, dict[str, str]] = {
    "seo_opportunity": {
        "improved":    "SEO-пересборка ранее стабилизировала CTR. Эффект сохранялся {window} дн.",
        "stabilized":  "Корректировка карточки ранее стабилизировала конверсию на {window} дн.",
        "temporary":   "Предыдущая пересборка дала кратковременный эффект ({window} дн.) — эффект не закрепился.",
        "failed":      "Предыдущие пересборки не дали устойчивого результата. Возможно, проблема не в карточке.",
        "repeated":    "CTR стабилизировался на {window} дн. Похожая ситуация вернулась.",
    },
    "high_ad_spend": {
        "improved":    "Снижение ставок ранее нормализовало ДРР на {window} дн.",
        "stabilized":  "Корректировка ставок ранее удерживала ДРР в норме {window} дн.",
        "temporary":   "Нагрузка вернулась спустя {window} дн. после корректировки ставок.",
        "failed":      "Снижение ставок ранее не давало устойчивого эффекта. Требуется ревизия структуры кампаний.",
        "repeated":    "Рекламная нагрузка стабилизировалась на {window} дн., затем вернулась.",
    },
    "margin_crisis": {
        "improved":    "Оптимизация затрат ранее восстанавливала маржу на {window} дн.",
        "stabilized":  "Структура затрат стабилизировалась на {window} дн. после вмешательства.",
        "temporary":   "Давление на маржу вернулось через {window} дн. — эффект был временным.",
        "failed":      "Предыдущие оптимизации не удержали маржу. Проблема структурная.",
        "repeated":    "Структура затрат снова вышла за устойчивый диапазон спустя {window} дн.",
    },
    "low_stock": {
        "improved":    "Пополнение ранее предотвращало out-of-stock на {window} дн.",
        "stabilized":  "Запас удерживался в норме {window} дн. после пополнения.",
        "temporary":   "Склад снова опустел через {window} дн. — пополнение дало краткосрочный эффект.",
        "failed":      "Ситуация с остатками повторяется. Порог пополнения требует пересмотра.",
        "repeated":    "Похожая нехватка запасов возвращается — паттерн регулярный.",
    },
    "high_rating": {
        "improved":    "Рейтинг держался на высоком уровне {window} дн. после предыдущих действий.",
        "stabilized":  "Позиция рейтинга оставалась стабильной {window} дн.",
        "temporary":   "Рейтинг временно снизился после {window} дн. стабильности.",
        "failed":      "Предыдущие меры не удержали рейтинг.",
        "repeated":    "Рейтинг снова требует внимания после {window} дн. стабильности.",
    },
    "sales_growth": {
        "improved":    "Рост сохранялся устойчиво {window} дн. после масштабирования.",
        "stabilized":  "Динамика роста удерживалась {window} дн.",
        "temporary":   "Рост замедлился через {window} дн. после активных действий.",
        "failed":      "Масштабирование ранее не давало устойчивого роста.",
        "repeated":    "Похожий рост наблюдался ранее — паттерн возобновляется.",
    },
}

_DEFAULT_TEMPLATES: dict[str, str] = {
    "improved":    "Предыдущее вмешательство дало устойчивый эффект на {window} дн.",
    "stabilized":  "Ситуация стабилизировалась на {window} дн. после прошлых действий.",
    "temporary":   "Эффект сохранялся {window} дн., затем ситуация повторилась.",
    "failed":      "Предыдущие действия не дали устойчивого результата.",
    "repeated":    "Паттерн ранее возвращался после {window} дн. стабилизации.",
}


def build_outcome_note(ev: OutcomeEvaluation) -> str:
    """Human-readable single-sentence outcome memory. Operational tone only."""
    tmpl = (
        _OUTCOME_TEMPLATES.get(ev.insight_type, _DEFAULT_TEMPLATES)
        .get(ev.outcome, _DEFAULT_TEMPLATES.get(ev.outcome, ""))
    )
    return tmpl.format(window=ev.evaluation_window_days)


# ── Evaluation engine ──────────────────────────────────────────────────────────

def detect_recurrence(
    category: str,
    resolved_at: datetime | None,
    notif_count: int,
    now: datetime | None = None,
) -> bool:
    """True if the pattern has demonstrably recurred."""
    now = now or datetime.utcnow()
    if resolved_at is not None:
        return True  # was resolved, now active again
    return notif_count >= 3


def evaluate_resolution_outcome(
    insight_key:    str,
    category:       str,
    resolved_at:    datetime | None,
    notif_count:    int,
    now:            datetime | None = None,
) -> OutcomeEvaluation | None:
    """
    Evaluate what happened after prior interventions for this insight.

    Returns None when there's no history (first occurrence).

    Sources:
      resolved_at   — InsightRecord.updated_at when status == "resolved"
      notif_count   — Telegram notification count (90-day window); may be 0 for non-Telegram users
    """
    now = now or datetime.utcnow()

    # No signal history → no outcome to report
    if resolved_at is None and notif_count < 2:
        return None

    half_life    = RESOLUTION_HALF_LIFE.get(category, 14)
    intervention = TYPICAL_INTERVENTION.get(category, "вмешательство")

    # Primary signal: insight was previously resolved but is active again
    if resolved_at is not None:
        days_since = int((now - resolved_at).total_seconds() / 86400)
        days_since = max(days_since, 1)

        if days_since * 2 < half_life:  # less than half the expected window → temporary
            # Effect lasted less than half the expected window → temporary
            return OutcomeEvaluation(
                insight_type=category,
                intervention=intervention,
                outcome="temporary",
                evaluation_window_days=days_since,
                metric_delta=None,
                explanation=f"{intervention} дала эффект на {days_since} дн. — менее ожидаемого.",
                confidence=72,
                recurrence_detected=True,
            )
        else:
            # Held for a reasonable period, then recurred → repeated
            return OutcomeEvaluation(
                insight_type=category,
                intervention=intervention,
                outcome="repeated",
                evaluation_window_days=days_since,
                metric_delta=None,
                explanation=f"Стабилизация держалась {days_since} дн., паттерн вернулся.",
                confidence=75,
                recurrence_detected=True,
            )

    # Secondary signal: seen 3+ times via Telegram, never resolved → failed
    if notif_count >= 3:
        return OutcomeEvaluation(
            insight_type=category,
            intervention=intervention,
            outcome="failed",
            evaluation_window_days=90,
            metric_delta=None,
            explanation=f"Паттерн наблюдается {notif_count}× за 90 дней без устойчивой стабилизации.",
            confidence=65,
            recurrence_detected=True,
        )

    return None


# ── Recommendation bias ────────────────────────────────────────────────────────

_FAILED_REC_OVERRIDES: dict[str, str] = {
    "seo_opportunity": (
        "Проверьте ценовое позиционирование — предыдущая пересборка не дала устойчивого эффекта"
    ),
    "high_ad_spend": (
        "Пересмотрите структуру кампаний — снижение ставок ранее давало временный эффект"
    ),
    "margin_crisis": (
        "Рассмотрите изменение ценовой модели — предыдущая оптимизация затрат не стабилизировала маржу"
    ),
    "low_stock": (
        "Настройте автоматический порог пополнения — ситуация повторяется"
    ),
}

_REPEATED_REC_PREFIX: dict[str, str] = {
    "high_ad_spend": "Паттерн возвращается после временной стабилизации — проверьте структуру кампаний",
    "margin_crisis": "Давление на маржу возобновляется — возможно, проблема структурная",
    "low_stock":     "Повторная нехватка: рассмотрите систематический порог пополнения",
    "seo_opportunity": "CTR снова снизился — оцените конкурентное окружение карточки",
}


def apply_outcome_to_recommendations(
    recommendations: list[str],
    category: str,
    outcome: str,
) -> list[str]:
    """
    Adjust recommendation priority based on outcome memory.
    Returns modified list (at most 4 items).
    """
    recs = list(recommendations)

    if outcome == "failed":
        override = _FAILED_REC_OVERRIDES.get(category)
        if override and recs:
            recs = [override] + recs[1:]

    elif outcome == "repeated":
        prefix = _REPEATED_REC_PREFIX.get(category)
        if prefix and prefix not in recs:
            recs = [prefix] + recs

    elif outcome == "temporary":
        # Same as failed for recommendation purposes
        override = _FAILED_REC_OVERRIDES.get(category)
        if override and recs:
            recs = [override] + recs[1:]

    return recs[:4]
