"""
Operational Recovery Paths — Sprint 36.

Models how operational pressure typically resolves — not prediction, not optimism.
Recovery intelligence: what recovers first, what lags, when it becomes structural.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class RecoveryPath:
    recovery_probability:          int            # 0-100
    recovery_state:                str            # quick | gradual | structural | unstable
    first_recovered_metric:        Optional[str]  # what normalizes fastest
    lagging_metric:                Optional[str]  # what stays unstable longest
    expected_recovery_window_days: Optional[int]  # approximate; None = high structural uncertainty
    recovery_note:                 Optional[str]  # restrained one-sentence narrative
    recovery_dependency:           Optional[str]  # precondition for recovery
    stabilization_pattern:         Optional[str]  # internal label for pattern type


# ── Recovery registry ─────────────────────────────────────────────────────────
# Keyed by insight category. Defines baseline recovery profile.
# Base state is adjusted downward by fragility/lifecycle/systemic context.

_REGISTRY: dict[str, dict] = {
    "seo_opportunity": {
        "recovery_state":                "quick",
        "first_recovered_metric":        "CTR",
        "lagging_metric":                "органическая позиция",
        "window_days":                   14,
        "recovery_note": (
            "После стабилизации карточки CTR обычно восстанавливается "
            "раньше органической позиции."
        ),
        "recovery_dependency": "stable indexing period",
        "stabilization_pattern": "card_quality_rebuild",
    },
    "high_ad_spend": {
        "recovery_state":                "gradual",
        "first_recovered_metric":        "рекламный расход",
        "lagging_metric":                "стабильность ROAS",
        "window_days":                   21,
        "recovery_note": (
            "Рекламная эффективность обычно нормализуется раньше "
            "полной стабилизации ROAS."
        ),
        "recovery_dependency": "unit-экономика без дефицита маржи",
        "stabilization_pattern": "ad_spend_normalization",
    },
    "margin_crisis": {
        "recovery_state":                "structural",
        "first_recovered_metric":        "вклад в маржу",
        "lagging_metric":                "стабильность повторных покупок",
        "window_days":                   45,
        "recovery_note": (
            "Текущее давление редко стабилизируется без пересмотра "
            "закупочной или ценовой модели."
        ),
        "recovery_dependency": "пересмотр ценовой модели",
        "stabilization_pattern": "pricing_model_revision",
    },
    "low_stock": {
        "recovery_state":                "quick",
        "first_recovered_metric":        "наличие товара",
        "lagging_metric":                "позиция в поиске",
        "window_days":                   10,
        "recovery_note": (
            "После восполнения запасов алгоритм обычно восстанавливает "
            "видимость постепенно."
        ),
        "recovery_dependency": "цикл поставки без разрывов",
        "stabilization_pattern": "supply_cycle_correction",
    },
    "sales_growth": {
        "recovery_state":                "unstable",
        "first_recovered_metric":        "скорость продаж",
        "lagging_metric":                "стабильность атрибуции",
        "window_days":                   30,
        "recovery_note": (
            "Даже после частичной стабилизации паттерн может "
            "возвращаться при росте нагрузки."
        ),
        "recovery_dependency": "баланс складского буфера",
        "stabilization_pattern": "inventory_buffer_alignment",
    },
    "price_pressure_cluster": {
        "recovery_state":                "structural",
        "first_recovered_metric":        "ценовая позиция",
        "lagging_metric":                "доля рынка",
        "window_days":                   60,
        "recovery_note": (
            "Ценовое давление системного характера редко рассеивается "
            "без стратегического пересмотра позиционирования."
        ),
        "recovery_dependency": "пересмотр ценовой стратегии",
        "stabilization_pattern": "price_repositioning",
    },
}

# ── Base probability by recovery state ────────────────────────────────────────
_BASE_PROB: dict[str, int] = {
    "quick":      78,
    "gradual":    62,
    "unstable":   51,
    "structural": 38,
}

# ── Narrative overrides for recurring/systemic context ───────────────────────
_STRUCTURAL_NOTE = (
    "Текущее давление редко стабилизируется без пересмотра закупочной или ценовой модели."
)
_UNSTABLE_NOTE = (
    "Даже после частичной стабилизации паттерн может возвращаться при росте нагрузки."
)


def _cat(key: str) -> str:
    return key.split(":")[0]


def compute_recovery_path(
    insight:            Any,
    lifecycle:          Optional[str],
    trajectory:         Optional[Any],   # OperationalTrajectory or enriched proxy
    portfolio_patterns: list,
    forecast:           Optional[Any] = None,  # FailureForecast or enriched proxy
) -> RecoveryPath:
    """
    Compute recovery path for a single insight.
    Uses registry baseline, adjusted by operational context.
    NEVER modifies signal scores.
    """
    cat = _cat(getattr(insight, "key", ""))
    rec = _REGISTRY.get(cat)

    if rec is None:
        return RecoveryPath(
            recovery_probability=55,
            recovery_state="gradual",
            first_recovered_metric=None,
            lagging_metric=None,
            expected_recovery_window_days=None,
            recovery_note=None,
            recovery_dependency=None,
            stabilization_pattern=None,
        )

    base_state     = rec["recovery_state"]
    outcome_state  = getattr(insight, "outcome_state", None)
    recurrence     = getattr(insight, "signal_recurrence_count", 0) or 0
    reversibility  = getattr(trajectory, "reversibility_state", None) if trajectory else None
    fragility      = getattr(forecast, "forecast_fragility_state", None) if forecast else None

    systemic = any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        for p in portfolio_patterns
    )

    # ── Escalate state based on context ──────────────────────────────────────
    state = base_state
    if base_state == "quick" and lifecycle == "recurring" and recurrence >= 2:
        state = "unstable"
    elif base_state == "gradual" and (systemic or (lifecycle == "recurring" and recurrence >= 3)):
        state = "structural"
    elif base_state == "unstable" and reversibility == "structurally_locked":
        state = "structural"

    # ── Probability computation ───────────────────────────────────────────────
    prob = _BASE_PROB.get(state, 55)

    if outcome_state == "improved":
        prob += 10
    if outcome_state == "repeated":
        prob -= 16
    if lifecycle == "recurring":
        prob -= 12
    if systemic:
        prob -= 14
    if reversibility == "easily_reversible":
        prob += 8
    if reversibility == "narrowing_window":
        prob -= 10
    if fragility == "stable":
        prob += 6
    if fragility == "critical":
        prob -= 10

    prob = max(0, min(100, prob))

    # ── Window adjustment ─────────────────────────────────────────────────────
    base_window = rec.get("window_days")
    if base_window is None:
        window_days = None
    elif state == "structural":
        window_days = None  # structural: high uncertainty, no window shown
    elif state == "unstable" and recurrence >= 2:
        window_days = int(base_window * 1.4)   # longer when recurring
    elif lifecycle == "recurring":
        window_days = int(base_window * 1.2)
    else:
        window_days = base_window

    # ── Note selection ────────────────────────────────────────────────────────
    if state == "structural":
        note = _STRUCTURAL_NOTE
    elif state == "unstable":
        note = _UNSTABLE_NOTE
    else:
        note = rec.get("recovery_note")

    return RecoveryPath(
        recovery_probability=prob,
        recovery_state=state,
        first_recovered_metric=rec.get("first_recovered_metric"),
        lagging_metric=rec.get("lagging_metric"),
        expected_recovery_window_days=window_days,
        recovery_note=note,
        recovery_dependency=rec.get("recovery_dependency"),
        stabilization_pattern=rec.get("stabilization_pattern"),
    )
