"""
Signal lifecycle tracking — Sprint 24.
Classifies each signal into one of 5 operational lifecycle stages based on history.
No ML, no predictions. Pure operational heuristics: age, repetition, resolution outcome.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Optional, Literal

# Half-life mirrors outcome_memory.py — same resolution decay model
RESOLUTION_HALF_LIFE: dict[str, int] = {
    "seo_opportunity": 18,
    "high_ad_spend":   11,
    "margin_crisis":   45,
    "low_stock":        7,
    "high_rating":     30,
    "sales_growth":    21,
}

LifecycleStage = Literal["emerging", "confirmed", "stabilized", "recurring", "resolved"]


@dataclasses.dataclass
class SignalLifecycle:
    stage:               LifecycleStage
    operational_meaning: str         # one-line operational description
    lifecycle_note:      str         # shown inside expanded card section
    lifecycle_weight:    int         # 5 | 15 | 20 | 55 | 85
    days_in_state:       int         # signal age in days (0 if first_seen unknown)
    recurrence_count:    int         # notification count / times signal returned
    stabilization_age:   Optional[int]  # days since last resolved; None = never resolved


def compute_signal_lifecycle(
    *,
    insight_key:     str,
    rule_category:   str,
    first_seen:      Optional[datetime],
    resolved_at:     Optional[datetime],
    notif_count:     int,
    outcome_state:   Optional[str],
    confidence_band: Optional[str],
) -> SignalLifecycle:
    """
    Returns a SignalLifecycle for a signal based on its operational history.

    Priority order (first matching rule wins):
      resolved → recurring → stabilized → confirmed → emerging
    """
    now = datetime.utcnow()

    days_in_state  = int((now - first_seen).total_seconds() / 86400) if first_seen else 0
    days_since_res = int((now - resolved_at).total_seconds() / 86400) if resolved_at else None
    half_life      = RESOLUTION_HALF_LIFE.get(rule_category, 21)

    # ── 1. resolved — recently closed, verifying stability ────────────────────
    if (
        outcome_state in ("improved", "stabilized")
        and days_since_res is not None
        and days_since_res < 7
    ):
        return SignalLifecycle(
            stage="resolved",
            operational_meaning="Сигнал недавно закрыт — ПУЛЬТ проверяет устойчивость",
            lifecycle_note=(
                "Сигнал был закрыт. ПУЛЬТ проверяет устойчивость результата."
            ),
            lifecycle_weight=5,
            days_in_state=days_in_state,
            recurrence_count=notif_count,
            stabilization_age=days_since_res,
        )

    # ── 2. recurring — signal returned after previous stabilization ───────────
    if (
        outcome_state == "repeated"
        or (outcome_state == "temporary" and notif_count >= 2)
        or (resolved_at is not None and notif_count >= 2)
    ):
        return SignalLifecycle(
            stage="recurring",
            operational_meaning="Паттерн возвращается после стабилизации — системная проблема",
            lifecycle_note=(
                "Паттерн повторяется после предыдущей стабилизации. "
                "Единичные меры не устраняют первопричину — требуется системное решение."
            ),
            lifecycle_weight=85,
            days_in_state=days_in_state,
            recurrence_count=notif_count,
            stabilization_age=days_since_res,
        )

    # ── 3. stabilized — previously resolved, in monitoring window ─────────────
    if (
        outcome_state in ("improved", "stabilized")
        and days_since_res is not None
        and days_since_res >= 7
        and days_since_res * 2 < half_life
    ):
        return SignalLifecycle(
            stage="stabilized",
            operational_meaning="Ранее стабилизировалось — мониторинг возврата активен",
            lifecycle_note=(
                f"Ранее стабилизировалось {days_since_res} дн. назад. "
                "Мониторинг возврата активен в течение операционного окна."
            ),
            lifecycle_weight=15,
            days_in_state=days_in_state,
            recurrence_count=notif_count,
            stabilization_age=days_since_res,
        )

    # ── 4. confirmed — mature signal with repeated observations ───────────────
    if notif_count >= 2 or (days_in_state >= 7 and confidence_band in ("stable", "high")):
        note_days = f" {days_in_state} дн." if days_in_state > 0 else ""
        return SignalLifecycle(
            stage="confirmed",
            operational_meaning="Паттерн подтверждён — операционное воздействие устойчиво",
            lifecycle_note=(
                f"Паттерн наблюдается{note_days} и подтверждён повторными наблюдениями. "
                "Операционное воздействие устойчиво."
            ),
            lifecycle_weight=55,
            days_in_state=days_in_state,
            recurrence_count=notif_count,
            stabilization_age=days_since_res,
        )

    # ── 5. emerging — new, unconfirmed signal ─────────────────────────────────
    age_str = f"{days_in_state} дн." if days_in_state > 1 else "менее суток"
    return SignalLifecycle(
        stage="emerging",
        operational_meaning="Сигнал в начальной фазе — требует подтверждения",
        lifecycle_note=(
            f"Сигнал наблюдается {age_str} — данных ещё недостаточно для полного подтверждения. "
            "ПУЛЬТ продолжит мониторинг."
        ),
        lifecycle_weight=20,
        days_in_state=days_in_state,
        recurrence_count=notif_count,
        stabilization_age=days_since_res,
    )
