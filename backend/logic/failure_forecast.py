"""
Operational Failure Forecasting — Sprint 35.

Models where current pressure is heading if left unstabilized.
NOT a prediction engine. NOT a deadline system.
Operational foresight: narrowing flexibility, growing fragility, probable next instability layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class FailureForecast:
    escalation_probability:    int            # 0-100
    instability_window_days:   Optional[int]  # approximate horizon; None = high uncertainty
    fragility_state:           str            # stable | sensitive | fragile | critical
    predicted_next_stage:      Optional[str]  # what operational phase likely follows
    first_failure_mode:        Optional[str]  # what breaks first if pressure persists
    forecast_note:             Optional[str]  # restrained one-sentence operational narrative
    forecast_confidence_band:  str            # low | moderate | stable | high


# ── Next-stage escalation map ─────────────────────────────────────────────────
_NEXT_STAGE: dict[str, str] = {
    "high_ad_spend":    "margin_crisis",
    "seo_opportunity":  "advertising_dependency",
    "margin_crisis":    "structural_margin_compression",
    "low_stock":        "ranking_loss",
    "sales_growth":     "inventory_pressure",
}

# ── First failure mode per category ──────────────────────────────────────────
_FIRST_FAILURE: dict[str, str] = {
    "high_ad_spend": (
        "Платный трафик перестанет компенсировать рост ставок"
    ),
    "margin_crisis": (
        "Маржинальность перейдёт в отрицательный диапазон"
    ),
    "seo_opportunity": (
        "Карточка потеряет органическую устойчивость"
    ),
    "low_stock": (
        "Алгоритм начнёт снижать видимость товара"
    ),
    "sales_growth": (
        "Складской буфер не выдержит темп роста"
    ),
    "price_pressure_cluster": (
        "Ценовой диапазон выйдет за конкурентную зону"
    ),
}

# ── Approximate instability windows by category (midpoint days) ───────────────
_WINDOW_BASE: dict[str, int] = {
    "seo_opportunity":        21,   # 14–30 days
    "high_ad_spend":          18,   # 10–25 days
    "margin_crisis":          14,   # 7–21 days
    "low_stock":               7,   # 3–10 days
    "sales_growth":           30,   # 20–45 days
    "price_pressure_cluster": 45,
}

# ── Forecast notes by fragility state ────────────────────────────────────────
_NOTES: dict[str, dict[str, str]] = {
    "stable": {
        "default": "Текущее давление остаётся управляемым при своевременном вмешательстве.",
    },
    "sensitive": {
        "default": "Давление пока остаётся обратимым, но окно стабилизации постепенно сужается.",
        "seo_opportunity": (
            "Паттерн постепенно приближается к фазе структурной зависимости от рекламы."
        ),
        "low_stock": "Запасы снижаются быстрее нормального цикла поставки.",
    },
    "fragile": {
        "default": (
            "Если текущее давление сохранится, система может перейти "
            "в следующую фазу нестабильности в течение 2–4 недель."
        ),
        "high_ad_spend": (
            "Рекламная нагрузка постепенно переходит в структурную зависимость "
            "от платного трафика."
        ),
        "margin_crisis": (
            "Если текущее давление сохранится, маржинальность может перейти "
            "в нестабильный диапазон в течение 2–4 недель."
        ),
    },
    "critical": {
        "default": (
            "Операционная гибкость существенно сократилась. "
            "Вмешательство на текущем этапе значительно эффективнее, чем после эскалации."
        ),
        "margin_crisis": (
            "Маржинальная нагрузка достигла уровня, при котором "
            "структурная компрессия становится вероятной без активного вмешательства."
        ),
        "high_ad_spend": (
            "Рекламная нагрузка достигла уровня системного давления на unit-экономику."
        ),
    },
}


def _cat(key: str) -> str:
    return key.split(":")[0]


def compute_failure_forecast(
    insight:             Any,
    lifecycle:           Optional[str],
    trajectory:          Optional[Any],     # OperationalTrajectory or None
    decay:               Optional[str],
    portfolio_patterns:  list,
    sequencing:          Optional[Any] = None,
) -> FailureForecast:
    """
    Compute failure forecast for a single insight.
    Uses lifecycle, trajectory, decay, and portfolio context.
    NEVER modifies confidence scores.
    """
    cat = _cat(getattr(insight, "key", ""))
    recurrence     = getattr(insight, "signal_recurrence_count", 0) or 0
    outcome_state  = getattr(insight, "outcome_state", None)
    traj_state     = getattr(trajectory, "trajectory_state", None) if trajectory else None
    reversibility  = getattr(trajectory, "reversibility_state", None) if trajectory else None
    pressure_acc   = getattr(trajectory, "pressure_accumulation", None) if trajectory else None

    systemic = any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        for p in portfolio_patterns
    )

    # ── Escalation probability ────────────────────────────────────────────────
    base: int
    if lifecycle == "emerging":
        base = 25
    elif lifecycle == "confirmed":
        base = 45
    elif lifecycle == "recurring":
        base = 68
    else:
        base = 35  # None / unknown

    if traj_state == "persistent":
        base += 12
    elif traj_state == "escalating":
        base += 20
    elif traj_state == "structurally_accumulating":
        base += 20  # same ceiling push

    if systemic:
        base += 15

    if reversibility == "narrowing_window":
        base += 10

    if outcome_state == "repeated" or recurrence >= 3:
        base += 8

    if decay == "fading":
        base -= 15

    if traj_state in ("stabilizing",) or outcome_state in ("improved", "stabilized"):
        base -= 20

    prob = max(0, min(100, base))

    # ── Fragility state ───────────────────────────────────────────────────────
    if prob <= 34:
        fragility = "stable"
    elif prob <= 54:
        fragility = "sensitive"
    elif prob <= 74:
        fragility = "fragile"
    else:
        fragility = "critical"

    # ── Instability window ────────────────────────────────────────────────────
    base_window = _WINDOW_BASE.get(cat)
    if base_window is None:
        window_days = None
    elif fragility == "stable":
        window_days = None  # no meaningful window when stable
    elif fragility == "critical":
        window_days = max(5, int(base_window * 0.6))  # compress ~40%
    elif fragility == "fragile":
        window_days = max(7, int(base_window * 0.8))
    else:
        window_days = base_window

    # ── Next stage ────────────────────────────────────────────────────────────
    predicted_next_stage = _NEXT_STAGE.get(cat) if fragility in ("fragile", "critical") else None

    # ── First failure mode ────────────────────────────────────────────────────
    first_failure_mode = (
        _FIRST_FAILURE.get(cat)
        if fragility in ("fragile", "critical")
        else None
    )

    # ── Forecast confidence ───────────────────────────────────────────────────
    if lifecycle in ("confirmed", "recurring") and decay is not None:
        conf_band = "stable"
    elif lifecycle == "emerging" or decay is None:
        conf_band = "moderate"
    elif lifecycle is None:
        conf_band = "low"
    else:
        conf_band = "moderate"

    # ── Forecast note ─────────────────────────────────────────────────────────
    frag_notes = _NOTES.get(fragility, {})
    note = frag_notes.get(cat) or frag_notes.get("default")

    return FailureForecast(
        escalation_probability=prob,
        instability_window_days=window_days,
        fragility_state=fragility,
        predicted_next_stage=predicted_next_stage,
        first_failure_mode=first_failure_mode,
        forecast_note=note,
        forecast_confidence_band=conf_band,
    )
