"""
Observability Recovery Forecast — Sprint 44.

Per-insight layer that answers: "When will this signal become interpretable again?"

NOT a restriction layer. Operational observability guidance.
Never uses urgency language. Never commands operator to stop.
Explains WHEN observability recovers and WHAT blocks it now.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class ObservabilityRecovery:
    obs_recovery_state:     str            # clear | recovering | distorted | fragmented | reset_required
    obs_recovery_window_days: Optional[int]  # estimated days to clean attribution
    obs_recovery_condition: Optional[str]  # restrained: what must happen
    obs_blocking_factor:    Optional[str]  # what currently prevents interpretation
    obs_recovery_note:      Optional[str]  # 1-sentence restrained narrative


# ── Window display bands ───────────────────────────────────────────────────────

def _window_label(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    if days <= 7:
        return "≈ 1 неделя"
    if days <= 14:
        return "≈ 1–2 недели"
    if days <= 21:
        return "≈ 2–3 недели"
    return "≈ 3–5 недель"


# ── Blocking factors ───────────────────────────────────────────────────────────

_BLOCKING: dict[str, str] = {
    "concurrent":  "параллельные вмешательства, снижающие точность атрибуции",
    "window":      "незавершённое стабилизационное окно наблюдения",
    "structural":  "структурное давление, маскирующее операционный сигнал",
    "escalating":  "накапливающееся давление затрудняет чистую атрибуцию",
}


# ── Recovery conditions ────────────────────────────────────────────────────────

_CONDITION: dict[str, str] = {
    "seo_opportunity":  "после стабилизации CTR и органической видимости",
    "high_ad_spend":    "после завершения attribution window рекламных кампаний",
    "margin_crisis":    "после выравнивания рекламной и ценовой базы",
    "low_stock":        "после нормализации запасов и складской видимости",
    "sales_growth":     "после стабилизации скорости продаж",
    "price_pressure_cluster": "после снижения ценовой волатильности портфеля",
}

_CONDITION_CONCURRENT = "после завершения параллельных стабилизационных окон"
_CONDITION_STRUCTURAL = "после завершения текущего стабилизационного окна"


# ── Narratives (restrained, never urgent) ─────────────────────────────────────

_NOTES: dict[str, str] = {
    "clear":         "",
    "recovering":    "Сигнал постепенно возвращается к интерпретируемому состоянию.",
    "distorted":     "Текущие изменения ещё не полностью отражены в сигнале. Выводы на этом этапе могут быть неточными.",
    "fragmented":    "Параллельные вмешательства временно снижают точность атрибуции.",
    "reset_required": "Для получения чистого сигнала требуется завершение текущего стабилизационного окна.",
}


# ── Core detection ────────────────────────────────────────────────────────────

def compute_observability_recovery(
    insight:               Any,
    concurrent_active:     int = 0,
) -> ObservabilityRecovery:
    """
    Compute observability recovery state for a single insight.

    Inputs (duck-typed from InsightItem enrichment):
      recovery_signal_state              — from Sprint 38 stabilization lock
      lock_estimated_recovery_window_days — from Sprint 38
      trajectory_state                   — from Sprint 33
      counterfactual_pressure_state      — from Sprint 39
      signal_decay_state                 — from Sprint 27
      confidence                         — base field
    """
    lock_state   = getattr(insight, "recovery_signal_state",                None)
    lock_window  = getattr(insight, "lock_estimated_recovery_window_days",   None)
    traj         = getattr(insight, "trajectory_state",                     None)
    cf_state     = getattr(insight, "counterfactual_pressure_state",        None)
    decay        = getattr(insight, "signal_decay_state",                   None)
    cat          = (getattr(insight, "key", "") or "").split(":")[0].replace("demo_", "")

    # ── State detection ───────────────────────────────────────────────────────

    if lock_state == "ready" or lock_state is None:
        return ObservabilityRecovery(
            obs_recovery_state="clear",
            obs_recovery_window_days=None,
            obs_recovery_condition=None,
            obs_blocking_factor=None,
            obs_recovery_note=None,
        )

    is_structural = (
        traj in ("structurally_accumulating",)
        or cf_state == "structurally_locked"
    )
    is_escalating = traj in ("escalating", "structurally_accumulating")

    if lock_state == "waiting":
        if concurrent_active >= 3:
            if is_structural or (lock_window or 0) > 21:
                state = "reset_required"
            else:
                state = "fragmented"
        elif is_structural or (lock_window or 0) > 21:
            state = "reset_required"
        else:
            state = "distorted"

    elif lock_state == "stabilizing":
        if is_escalating and (lock_window or 0) > 14:
            state = "distorted"
        else:
            state = "recovering"

    elif lock_state == "reopening":
        state = "recovering"

    else:
        state = "clear"

    if state == "clear":
        return ObservabilityRecovery(
            obs_recovery_state="clear",
            obs_recovery_window_days=None,
            obs_recovery_condition=None,
            obs_blocking_factor=None,
            obs_recovery_note=None,
        )

    # ── Window ────────────────────────────────────────────────────────────────

    window = lock_window

    # ── Blocking factor ───────────────────────────────────────────────────────

    if concurrent_active >= 3:
        blocking = _BLOCKING["concurrent"]
    elif is_structural:
        blocking = _BLOCKING["structural"]
    elif is_escalating:
        blocking = _BLOCKING["escalating"]
    elif (lock_window or 0) > 0:
        blocking = _BLOCKING["window"]
    else:
        blocking = None

    # ── Recovery condition ─────────────────────────────────────────────────────

    if concurrent_active >= 3:
        condition = _CONDITION_CONCURRENT
    elif is_structural:
        condition = _CONDITION_STRUCTURAL
    else:
        condition = _CONDITION.get(cat, _CONDITION_STRUCTURAL)

    # ── Note ──────────────────────────────────────────────────────────────────

    note = _NOTES.get(state, "")

    return ObservabilityRecovery(
        obs_recovery_state=state,
        obs_recovery_window_days=window,
        obs_recovery_condition=condition,
        obs_blocking_factor=blocking,
        obs_recovery_note=note if note else None,
    )


# ── Window label helper (exported for UI use via response) ────────────────────

def obs_window_label(days: Optional[int]) -> Optional[str]:
    return _window_label(days)
