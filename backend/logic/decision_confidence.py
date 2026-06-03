"""
Decision Confidence Score — Sprint 23.

Operational certainty per insight. Not prediction. Not AI score.
Assembled from: signal stability, marketplace behavior, outcome history,
portfolio pressure, automation level, stabilization half-life.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DecisionConfidence:
    score:               int   # 0–100
    confidence_band:     Literal["low", "moderate", "stable", "high"]
    primary_driver:      str   # single phrase naming the dominant factor
    degrading_factors:   list[str]
    stabilizing_factors: list[str]
    explanation:         str   # short operational explanation (1–2 sentences)
    stability_note:      str   # slightly longer note shown in UI (1–2 sentences)


# ── Bands ──────────────────────────────────────────────────────────────────────

def _band(score: int) -> Literal["low", "moderate", "stable", "high"]:
    if score >= 75: return "high"
    if score >= 55: return "stable"
    if score >= 35: return "moderate"
    return "low"


# ── Pattern deltas ─────────────────────────────────────────────────────────────

_PATTERN_DELTA: dict[str, int] = {
    # Destabilizing
    "advertising_spike":       -8,
    "organic_recovery_lag":    -8,
    "reindexing_instability":  -8,
    "attribution_delay":      -12,
    "boost_attribution_gap":  -12,
    "price_oscillation_penalty": -8,
    "logistics_sla_impact":    -5,
    "cpc_bid_volatility":      -6,
    "organic_seo_stability":   -5,
    "review_moderation_lag":   -4,
    "price_index_check":       -4,
    "search_algo_update":      -4,
    "review_quality_gate":     -4,
    "margin_pressure_ads":     -8,
    # Stabilizing
    "stock_position_coupling":  +3,
}

_PATTERN_LABELS: dict[str, str] = {
    "advertising_spike":        "нестабильность рекламного CTR на WB",
    "organic_recovery_lag":     "задержка восстановления органики",
    "reindexing_instability":   "нестабильность переиндексации WB",
    "attribution_delay":        "задержка атрибуции Ozon",
    "boost_attribution_gap":    "расхождение окон атрибуции рекламы Ozon",
    "price_oscillation_penalty":"штраф за нестабильное ценообразование YM",
    "logistics_sla_impact":     "влияние SLA на рейтинг YM",
    "cpc_bid_volatility":       "волатильность ставок CPC на YM",
    "organic_seo_stability":    "медленная переиндексация YM",
    "review_moderation_lag":    "задержка модерации отзывов Ozon",
    "price_index_check":        "частые проверки ценового индекса Ozon",
    "search_algo_update":       "алгоритмическое обновление поиска Ozon",
    "review_quality_gate":      "порог рейтинга YM",
    "margin_pressure_ads":      "структурное рекламное давление на маржу WB",
    "stock_position_coupling":  "предсказуемая механика остатков WB",
}


# ── Explanation templates ──────────────────────────────────────────────────────

_BAND_EXPLANATIONS: dict[Literal["low", "moderate", "stable", "high"], list[str]] = {
    "high": [
        "Сигнал подтверждается устойчивой операционной историей.",
        "Паттерн демонстрирует стабильность и предсказуемость восстановления.",
    ],
    "stable": [
        "Сигнал операционно устойчив, отдельные факторы снижают определённость.",
        "Динамика подтверждена, но содержит элементы неопределённости.",
    ],
    "moderate": [
        "Сигнал присутствует, но операционная уверенность ограничена.",
        "Ряд факторов снижает достоверность — требуется дополнительное подтверждение.",
    ],
    "low": [
        "Сигнал нестабилен: несколько факторов снижают операционную уверенность.",
        "Требуется дополнительное подтверждение — история и механика площадки неоднозначны.",
    ],
}

_OUTCOME_STABILITY_NOTES: dict[str, str] = {
    "improved":   "Сигнал подтверждается предыдущими стабилизациями и не демонстрирует быстрого возврата.",
    "stabilized": "Исторически аналогичные ситуации стабилизировались устойчиво.",
    "temporary":  "Предыдущее вмешательство дало временный эффект — паттерн вернулся.",
    "failed":     "Предыдущие действия не дали устойчивого результата для этого типа проблемы.",
    "repeated":   "Паттерн ранее возвращался после временного улучшения.",
}

_PORTFOLIO_NOTES: dict[str, str] = {
    "systemic":  "Системное давление на уровне портфеля снижает операционную уверенность.",
    "moderate":  "Смежные товары демонстрируют схожую динамику — возможна категорийная нестабильность.",
    "localized": "Сигнал изолирован от портфельных паттернов.",
}

_MARKETPLACE_NOTES: dict[str, str] = {
    "attribution_delay":        "Площадка демонстрирует задержку атрибуции, снижающую достоверность сигнала.",
    "advertising_spike":        "Рекламная механика площадки вносит краткосрочную нестабильность в сигнал.",
    "reindexing_instability":   "Площадка демонстрирует нестабильное восстановление органики после изменений.",
    "organic_recovery_lag":     "Восстановление органического трафика характерно происходит с задержкой.",
    "price_oscillation_penalty":"Площадка штрафует нестабильное ценообразование — сигнал усилен механикой.",
    "boost_attribution_gap":    "Разные форматы рекламы площадки имеют разные окна атрибуции.",
}


# ── Core engine ────────────────────────────────────────────────────────────────

def compute_decision_confidence(
    *,
    confidence:                    int,
    signal_state:                  str | None,
    outcome_state:                  str | None,
    marketplace_patterns:          list[str],
    marketplace_stabilization_window: int | None,
    automation_level:              str | None,
    portfolio_complexity:          str | None,   # "systemic" | "moderate" | "localized" | None
    adaptation_note:               str | None,
) -> DecisionConfidence:
    """
    Compute operational certainty from 6 factor groups.
    Base = insight.confidence. Apply deltas. Clamp [0, 100].
    """
    score = confidence
    degrading: list[tuple[int, str]]   = []  # (delta, label)
    stabilizing: list[tuple[int, str]] = []

    # A. Signal state ─────────────────────────────────────────────────────────
    _ss_delta = {
        "structural": +8,
        "persistent": +4,
        "temporary":  -8,
    }.get(signal_state or "", 0)
    if _ss_delta > 0:
        stabilizing.append((_ss_delta, "структурно устойчивый сигнал"))
    elif _ss_delta < 0:
        degrading.append((_ss_delta, "временный или новый сигнал"))
    score += _ss_delta

    # B. Outcome history ──────────────────────────────────────────────────────
    _out_delta = {
        "improved":   +12,
        "stabilized": +8,
        "temporary":  -8,
        "failed":    -18,
        "repeated":  -22,
    }.get(outcome_state or "", 0)
    if _out_delta > 0:
        stabilizing.append((_out_delta, "подтверждена историей стабилизации"))
    elif _out_delta < 0:
        label = {
            "temporary": "предыдущий эффект был кратковременным",
            "failed":    "предыдущие вмешательства не дали результата",
            "repeated":  "паттерн исторически возвращается",
        }.get(outcome_state or "", "нестабильная история")
        degrading.append((_out_delta, label))
    score += _out_delta

    # C. Marketplace behavior ─────────────────────────────────────────────────
    mp_total = 0
    for p in marketplace_patterns:
        delta = _PATTERN_DELTA.get(p, 0)
        label = _PATTERN_LABELS.get(p, p)
        if delta > 0:
            stabilizing.append((delta, label))
        elif delta < 0:
            degrading.append((delta, label))
        mp_total += delta
    # no patterns at all: small boost
    if not marketplace_patterns:
        mp_total += 5
        stabilizing.append((5, "нет известных механик нестабильности"))
    # cap marketplace contribution at [-18, +15]
    mp_total = max(-18, min(15, mp_total))
    score += mp_total

    # D. Portfolio pressure ───────────────────────────────────────────────────
    _port_delta = {
        "systemic":  -14,
        "moderate":  -6,
        "localized": -3,
    }.get(portfolio_complexity or "", +4)
    if _port_delta > 0:
        stabilizing.append((_port_delta, "изолированная проблема без портфельного давления"))
    else:
        label = {
            "systemic":  "системное давление на уровне портфеля",
            "moderate":  "смежные товары демонстрируют схожую динамику",
            "localized": "локальный портфельный контекст",
        }.get(portfolio_complexity or "", "")
        if label:
            degrading.append((_port_delta, label))
    score += _port_delta

    # E. Automation level ─────────────────────────────────────────────────────
    _auto_delta = {
        "safe_auto":      +5,
        "human_required": -2,
        "blocked":        -8,
        "delayed":        -4,
        "critical_alert": -6,
    }.get(automation_level or "", 0)
    if _auto_delta > 0:
        stabilizing.append((_auto_delta, "вмешательство безопасно автоматизировано"))
    elif _auto_delta < 0:
        label = {
            "human_required": "требуется ручное подтверждение",
            "blocked":        "вмешательство заблокировано",
            "delayed":        "действие отложено — данные ещё не зрелые",
            "critical_alert": "критический контекст",
        }.get(automation_level or "", "")
        if label:
            degrading.append((_auto_delta, label))
    score += _auto_delta

    # Adaptation note penalty (operator had non-standard behavior)
    if adaptation_note:
        score -= 4
        degrading.append((-4, "потребовалась адаптация рекомендаций"))

    # F. Half-life ────────────────────────────────────────────────────────────
    win = marketplace_stabilization_window
    _hl_delta = (
        +8 if win is not None and win >= 30 else
        +4 if win is not None and win >= 14 else
         0 if win is not None and win >= 7  else
        -5 if win is not None               else
         0
    )
    if _hl_delta > 0:
        stabilizing.append((_hl_delta, f"исторически устойчивое окно стабилизации ({win} дн.)"))
    elif _hl_delta < 0:
        stabilizing_note = f"короткое окно стабилизации площадки ({win} дн.)"
        degrading.append((_hl_delta, stabilizing_note))
    score += _hl_delta

    score = max(0, min(100, score))
    band  = _band(score)

    # ── Primary driver (largest |delta|) ──────────────────────────────────────
    all_factors = degrading + [(d, l) for d, l in stabilizing]
    if all_factors:
        primary = max(all_factors, key=lambda x: abs(x[0]))
        primary_driver = primary[1]
    else:
        primary_driver = "операционный анализ сигнала"

    # ── Explanation ───────────────────────────────────────────────────────────
    exp_pool = _BAND_EXPLANATIONS.get(band, _BAND_EXPLANATIONS["moderate"])
    explanation = exp_pool[0]

    # ── Stability note ────────────────────────────────────────────────────────
    note = ""
    if outcome_state and outcome_state in _OUTCOME_STABILITY_NOTES:
        note = _OUTCOME_STABILITY_NOTES[outcome_state]
    elif marketplace_patterns:
        for p in marketplace_patterns:
            if p in _MARKETPLACE_NOTES:
                note = _MARKETPLACE_NOTES[p]
                break
    if not note and portfolio_complexity in _PORTFOLIO_NOTES:
        note = _PORTFOLIO_NOTES[portfolio_complexity]
    if not note:
        note = exp_pool[1] if len(exp_pool) > 1 else exp_pool[0]

    return DecisionConfidence(
        score=score,
        confidence_band=band,
        primary_driver=primary_driver,
        degrading_factors=[l for _, l in sorted(degrading, key=lambda x: x[0])],
        stabilizing_factors=[l for _, l in sorted(stabilizing, key=lambda x: -x[0])],
        explanation=explanation,
        stability_note=note,
    )
