"""
Intervention Reversal Intelligence — Sprint 49.

Identifies when a stabilization intervention has passed its peak utility,
when the system is showing signs of overcorrection, and when a controlled
rollback would be more stabilizing than continued escalation.

NOT anti-action layer. NOT blame system.
Operational diminishing return intelligence + rollback economics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InterventionReversal:
    reversal_state:              str            # stable_intervention | diminishing_return | overextended | reversal_window | structurally_locked
    reversal_probability:        int            # 0–100; not shown in UI as score
    reversal_window_days:        Optional[int]  # approximate; displayed as label
    reversal_trigger:            Optional[str]  # what is driving the reversal signal
    reversal_note:               Optional[str]  # restrained narrative
    rollback_safety:             str            # safe | conditional | risky | blocked
    rollback_effect_expectation: Optional[str]  # what to expect if partial rollback is applied
    stabilization_dependency:    Optional[str]  # only for structurally_locked


# ── Reversal notes ────────────────────────────────────────────────────────────
_REVERSAL_NOTES: dict[str, Optional[str]] = {
    "stable_intervention": None,
    "diminishing_return":  "Текущее вмешательство постепенно приближается к фазе снижающейся отдачи.",
    "overextended":        "Дальнейшее усиление может повысить операционную волатильность.",
    "reversal_window":     "Появилось окно для безопасного ослабления текущего вмешательства.",
    "structurally_locked": "Система накопила зависимость от текущей модели стабилизации — откат требует пошагового подхода.",
}

# ── Rollback safety by state ──────────────────────────────────────────────────
_ROLLBACK_SAFETY: dict[str, str] = {
    "stable_intervention": "conditional",
    "diminishing_return":  "conditional",
    "overextended":        "risky",
    "reversal_window":     "safe",
    "structurally_locked": "blocked",
}

# ── Base reversal probability ─────────────────────────────────────────────────
_BASE_PROB: dict[str, int] = {
    "stable_intervention": 15,
    "diminishing_return":  45,
    "overextended":        70,
    "reversal_window":     72,
    "structurally_locked": 82,
}

# ── Category-specific trigger notes ──────────────────────────────────────────
_REVERSAL_TRIGGERS: dict[str, str] = {
    "high_ad_spend":          "Рекламная нагрузка продолжает накапливаться без пропорционального улучшения конверсии",
    "margin_crisis":          "Структурное давление продолжает нарастать, несмотря на вмешательства",
    "sales_growth":           "Ускорение роста начинает создавать нагрузку на операционную устойчивость",
    "seo_opportunity":        "SEO-улучшения приближаются к операционному потолку",
    "price_pressure_cluster": "Ценовые корректировки продолжают создавать волатильность",
    "low_stock":              "Поставочные циклы не стабилизировались при текущем уровне вмешательства",
}

# ── Category-specific rollback effect expectations ───────────────────────────
_ROLLBACK_EFFECTS: dict[str, str] = {
    "high_ad_spend":          "Операционная волатильность может снизиться после ослабления рекламной нагрузки.",
    "margin_crisis":          "Возможна временная просадка оборота на этапе структурной стабилизации.",
    "seo_opportunity":        "Эффект станет наблюдаемым после восстановления окна наблюдаемости.",
    "sales_growth":           "Темп роста может временно замедлиться в процессе стабилизации.",
    "price_pressure_cluster": "Операционная волатильность может снизиться после ослабления ценового давления.",
    "low_stock":              "Поставочный ритм постепенно восстановится после нормализации запасов.",
}


def compute_intervention_reversal(
    insight:                object,
    trajectory_state:       Optional[str],
    trajectory_direction:   Optional[str],
    recovery_state:         Optional[str],
    recovery_probability:   Optional[int],
    outcome_state:          Optional[str],
    pressure_accumulation:  Optional[str],
    reversibility_state:    Optional[str],
    tradeoff_severity:      Optional[str],
    signal_lifecycle_stage: Optional[str],
    signal_recurrence_count: Optional[int],
    stabilization_window_days: Optional[int],
) -> InterventionReversal:
    cat       = getattr(insight, "key", "").split(":")[0].replace("demo_", "")
    recurrence = signal_recurrence_count or 0

    # ── State classification — highest severity wins ──────────────────────────

    # structurally_locked: deeply embedded, risky to exit
    if (
        reversibility_state == "structurally_locked"
        and outcome_state in ("failed", "repeated")
        and recurrence >= 3
    ):
        state = "structurally_locked"

    # overextended: intervention actively creating secondary pressure
    elif (
        trajectory_state in ("escalating", "structurally_accumulating")
        and tradeoff_severity in ("moderate", "significant")
        and outcome_state in ("repeated", "failed")
    ):
        state = "overextended"

    # reversal_window: intervention worked, safe to ease off now
    elif (
        recovery_state == "quick"
        and outcome_state in ("improved", "stabilized")
        and (recovery_probability or 0) > 60
        and trajectory_state in ("reversible", "stabilizing")
    ):
        state = "reversal_window"

    # diminishing_return: still working but effect slowing
    elif (
        (trajectory_state == "persistent" and outcome_state == "temporary")
        or (recovery_state == "unstable" and recurrence >= 2)
        or (
            pressure_accumulation == "accumulating"
            and outcome_state in ("temporary", "stabilized")
            and recurrence >= 2
        )
    ):
        state = "diminishing_return"

    else:
        state = "stable_intervention"

    # ── Reversal probability ──────────────────────────────────────────────────
    prob = _BASE_PROB[state]
    if recurrence >= 3:
        prob += 10
    if outcome_state == "repeated":
        prob += 8
    elif outcome_state in ("improved", "stabilized"):
        prob -= 10
    if pressure_accumulation == "compounding":
        prob += 5
    prob = max(5, min(95, prob))

    # ── Reversal window days ──────────────────────────────────────────────────
    base = stabilization_window_days
    if state == "reversal_window":
        window: Optional[int] = base or 14
    elif state == "overextended":
        window = max(5, int((base or 14) * 0.6)) if base else 7
    elif state == "diminishing_return":
        window = base
    else:
        window = None

    # ── Trigger and effect (only for non-stable, non-reversal_window) ─────────
    trigger = (
        _REVERSAL_TRIGGERS.get(cat)
        if state in ("diminishing_return", "overextended", "structurally_locked")
        else None
    )
    effect = (
        _ROLLBACK_EFFECTS.get(cat)
        if state in ("reversal_window", "overextended", "structurally_locked")
        else None
    )
    dependency = (
        "Система накопила зависимость от текущей модели стабилизации — резкий откат может дестабилизировать смежные сигналы."
        if state == "structurally_locked" else None
    )

    return InterventionReversal(
        reversal_state=state,
        reversal_probability=prob,
        reversal_window_days=window,
        reversal_trigger=trigger,
        reversal_note=_REVERSAL_NOTES[state],
        rollback_safety=_ROLLBACK_SAFETY[state],
        rollback_effect_expectation=effect,
        stabilization_dependency=dependency,
    )
