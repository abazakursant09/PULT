"""
Stabilization Lock — Sprint 38.

Models the observation window state for each insight:
how long the system needs to watch consequences of recent changes
before new interventions produce interpretable signal.

NOT a restriction layer. Stabilization pacing intelligence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class StabilizationLock:
    recovery_signal_state:          str            # waiting | stabilizing | reopening | ready
    estimated_recovery_window_days: Optional[int]  # how long until clean attribution
    reentry_condition:              Optional[str]  # what signal to wait for
    next_safe_action:               Optional[str]  # first safe intervention after window


# ── Attribution observation windows by category (days) ───────────────────────
_OBS_WINDOW: dict[str, int] = {
    "seo_opportunity":  7,    # indexing cycle
    "high_ad_spend":    10,   # attribution window
    "margin_crisis":    14,   # price + ad re-stabilization
    "low_stock":         3,   # fast feedback
    "sales_growth":     10,
    "price_pressure_cluster": 21,
}

# ── Reentry conditions per category ──────────────────────────────────────────
_REENTRY: dict[str, str] = {
    "seo_opportunity":  "После стабилизации CTR и органической позиции.",
    "high_ad_spend":    "После завершения attribution window рекламной кампании.",
    "margin_crisis":    "После выравнивания рекламной нагрузки и ценовой базы.",
    "low_stock":        "После восполнения запасов и нормализации видимости.",
    "sales_growth":     "После стабилизации скорости продаж и запасов.",
    "price_pressure_cluster": "После снижения ценовой волатильности по портфелю.",
}

# ── Next safe action per category ─────────────────────────────────────────────
_NEXT_SAFE: dict[str, str] = {
    "seo_opportunity":  "Можно будет отдельно проверить SEO-гипотезу.",
    "high_ad_spend":    "После стабилизации рекламы станет возможна корректировка цены.",
    "margin_crisis":    "Следующий пересмотр экономики лучше проводить после завершения текущего окна наблюдения.",
    "low_stock":        "После восполнения запасов можно будет оценить эффект на видимость.",
    "sales_growth":     "После стабилизации цикла поставки можно будет масштабировать рекламу.",
}

# ── Collision note for portfolio pressure ────────────────────────────────────
_PORTFOLIO_REENTRY = "После снижения параллельных изменений."
_PORTFOLIO_NEXT    = "Следующее вмешательство лучше проводить по завершении текущих стабилизаций."


def _cat(key: str) -> str:
    return key.split(":")[0]


def compute_stabilization_lock(
    insight:                  Any,
    lifecycle:                Optional[str],
    trajectory_state:         Optional[str],
    decay:                    Optional[str],
    recovery_state:           Optional[str],
    concurrent_active_count:  int   = 0,
    age_days:                 int   = 0,
    capacity_state:           str   = "stable",
) -> StabilizationLock:
    """
    Determine observation window state for a single insight.

    States:
      waiting     — attribution still noisy, changes too recent
      stabilizing — metrics becoming interpretable, volatility decreasing
      reopening   — system nearing observability recovery
      ready       — attribution clarity restored, interventions safe again
    """
    cat = _cat(getattr(insight, "key", ""))

    # ── State determination ───────────────────────────────────────────────────
    is_overloaded = capacity_state in ("saturated", "overloaded")

    if (
        (is_overloaded and age_days < 7)
        or (trajectory_state in ("escalating", "structurally_accumulating") and decay == "fresh" and age_days < 5)
        or (concurrent_active_count >= 3 and age_days < 7)
    ):
        state = "waiting"

    elif (
        (trajectory_state in ("persistent", "escalating") and decay in ("fresh", "aging", "persistent"))
        or (lifecycle == "recurring" and age_days < 21)
        or (is_overloaded and age_days < 14)
    ):
        state = "stabilizing"

    elif (
        trajectory_state in ("reversible", "stabilizing")
        or decay == "fading"
        or (age_days > 21 and trajectory_state not in ("escalating", "structurally_accumulating"))
    ):
        state = "reopening"

    else:
        state = "ready"

    # Override to ready for fast-clearing cases
    if decay in ("stale",) and trajectory_state not in ("escalating", "structurally_accumulating"):
        state = "ready"

    # Already stabilized insights → ready
    if trajectory_state == "stabilizing" and decay == "fading":
        state = "ready"

    # ── Window calculation ────────────────────────────────────────────────────
    base_window = _OBS_WINDOW.get(cat)
    if base_window is None or state == "ready":
        window_days = None
    elif state == "waiting":
        window_days = base_window
    elif state == "stabilizing":
        window_days = max(3, int(base_window * 0.6))
    elif state == "reopening":
        window_days = max(2, int(base_window * 0.3))
    else:
        window_days = None

    # ── Reentry condition ─────────────────────────────────────────────────────
    if concurrent_active_count >= 3:
        reentry = _PORTFOLIO_REENTRY
    else:
        reentry = _REENTRY.get(cat) if state in ("waiting", "stabilizing") else None

    # ── Next safe action ──────────────────────────────────────────────────────
    if state == "ready":
        next_safe = None
    elif concurrent_active_count >= 3:
        next_safe = _PORTFOLIO_NEXT
    else:
        next_safe = _NEXT_SAFE.get(cat)

    return StabilizationLock(
        recovery_signal_state=state,
        estimated_recovery_window_days=window_days,
        reentry_condition=reentry,
        next_safe_action=next_safe,
    )
