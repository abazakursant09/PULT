"""
Strategic Memory Drift Intelligence — Sprint 54.
Detects when current stabilization strategy diverges from historically effective recovery doctrine.
NOT behavioral coaching. NOT blame. NOT optimization AI.
Institutional, historically grounded, operationally mature.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# State → drift_note
_DRIFT_NOTES: dict[str, str] = {
    "aligned":
        "Стабилизационная траектория сохраняет преемственность с ранее устойчивыми сценариями восстановления.",
    "drifting":
        "Текущая стабилизация постепенно расходится с ранее устойчивыми сценариями восстановления.",
    "fragmented":
        "Повторяющиеся циклы стабилизации начинают использовать несовместимые операционные сценарии.",
    "historically_disconnected":
        "Текущая стратегия стабилизации слабо соотносится с исторически эффективными сценариями восстановления.",
    "compounding_repetition":
        "Система воспроизводит ранее неустойчивые сценарии восстановления без признаков адаптации.",
}

# State → doctrine_alignment_note
_DOCTRINE_NOTES: dict[str, str] = {
    "aligned":
        "Текущий подход к стабилизации соотносится с ранее эффективными сценариями восстановления.",
    "drifting":
        "Стабилизационная траектория начинает расходиться с ранее успешными операционными сценариями.",
    "fragmented":
        "Исторически устойчивые сценарии стабилизации утрачивают преемственность.",
    "historically_disconnected":
        "Текущая стратегия слабо соотносится с исторически эффективными сценариями восстановления.",
    "compounding_repetition":
        "Повторяющиеся циклы воспроизводят ранее неустойчивые сценарии без признаков адаптации.",
}

# State → memory_continuity
_CONTINUITY: dict[str, str] = {
    "aligned":                 "connected",
    "drifting":                "partially_connected",
    "fragmented":              "fragmented",
    "historically_disconnected": "disconnected",
    "compounding_repetition":  "disconnected",
}

# Category-specific repetition_pattern_note for compounding_repetition / fragmented
_REPETITION_PATTERNS: dict[str, str] = {
    "margin_crisis":
        "Цикл маржинального давления повторяется без улучшения структурной устойчивости.",
    "high_ad_spend":
        "Рекламная нагрузка продолжает воспроизводить предыдущие циклы без коррекции операционного подхода.",
    "low_stock":
        "Складской дефицит повторяется без признаков изменения операционной модели.",
    "seo_opportunity":
        "SEO-динамика воспроизводит повторяющийся паттерн без операционных улучшений.",
    "high_rating":
        "Рейтинговая динамика повторяет ранее наблюдаемые паттерны.",
    "sales_growth":
        "Цикл роста продолжает воспроизводить предыдущие структурные паттерны.",
}

_DEFAULT_REPETITION_PATTERN = "Повторяющиеся вмешательства не формируют устойчивого операционного паттерна."


@dataclass
class StrategicMemoryDrift:
    drift_state:             str
    memory_continuity:       str
    doctrine_alignment_note: Optional[str]
    repetition_pattern_note: Optional[str]
    drift_note:              str
    drift_confidence:        int
    historical_cycles:       int


def compute_strategic_memory_drift(
    insight_category:          str,
    signal_lifecycle_stage:    Optional[str],
    signal_recurrence_count:   Optional[int],
    outcome_state:             Optional[str],
    recovery_state:            Optional[str],
    recovery_probability:      Optional[int],
    adaptive_capacity_state:   Optional[str],
    resilience_trajectory:     Optional[str],
    reversal_state:            Optional[str],
    timing_state:              Optional[str],
    obs_recovery_state:        Optional[str],
    counterfactual_pressure_state: Optional[str],
    pressure_accumulation:     Optional[str],
    trajectory_direction:      Optional[str],
) -> StrategicMemoryDrift:
    """
    Classify strategic memory drift for a single insight.
    Priority: compounding_repetition > historically_disconnected > fragmented > drifting > aligned.
    """
    rec_count    = signal_recurrence_count or 0
    is_recurring = signal_lifecycle_stage in ("recurring", "confirmed", "persistent")
    cycles       = min(rec_count + 1, 6) if is_recurring else 1

    if not is_recurring:
        return _build("aligned", 50, cycles, insight_category, repetition=False)

    is_failed_outcome  = outcome_state in ("failed", "repeated")
    is_unstable_recov  = recovery_state in ("structural", "unstable")
    is_deteriorating   = adaptive_capacity_state == "deteriorating"
    is_rigid           = adaptive_capacity_state in ("rigid", "deteriorating")
    is_struct_degrading = resilience_trajectory == "structurally_degrading"

    # ── compounding_repetition ─────────────────────────────────────────────────
    # Actively repeating what historically didn't work; each cycle reinforces failure
    if (
        is_failed_outcome and rec_count >= 3 and is_deteriorating
    ) or (
        outcome_state == "repeated" and rec_count >= 2 and is_deteriorating
    ) or (
        is_failed_outcome and rec_count >= 2 and is_struct_degrading
    ):
        return _build("compounding_repetition", 85, cycles, insight_category, repetition=True)

    # ── historically_disconnected ──────────────────────────────────────────────
    # Current approach weakly connected to any historically effective pattern
    if (
        is_failed_outcome and rec_count >= 2 and is_rigid and is_unstable_recov
    ) or (
        is_struct_degrading and is_failed_outcome and rec_count >= 2
    ) or (
        is_rigid and is_unstable_recov and rec_count >= 3
        and counterfactual_pressure_state in ("compounding", "narrowing_window", "structurally_locked")
    ):
        return _build("historically_disconnected", 78, cycles, insight_category, repetition=False)

    # ── fragmented ─────────────────────────────────────────────────────────────
    # Multiple switching stabilization paths; no consistent doctrine
    frag_flags = sum([
        is_rigid and recovery_state == "unstable" and rec_count >= 2,
        reversal_state in ("overextended", "structurally_locked") and rec_count >= 2,
        timing_state == "structurally_late" and obs_recovery_state in ("fragmented", "distorted"),
        outcome_state == "temporary" and rec_count >= 3 and recovery_state != "quick",
        resilience_trajectory in ("degrading",) and is_rigid and rec_count >= 2,
    ])
    if frag_flags >= 1:
        return _build("fragmented", 72, cycles, insight_category, repetition=True)

    # ── drifting ───────────────────────────────────────────────────────────────
    # Gradual divergence; no fragmentation yet
    drift_flags = sum([
        adaptive_capacity_state == "plateauing" and counterfactual_pressure_state in ("narrowing_window", "compounding"),
        outcome_state == "temporary" and rec_count >= 2,
        recovery_state == "gradual" and resilience_trajectory in ("degrading",),
        pressure_accumulation == "accumulating" and trajectory_direction in ("worsening",) and rec_count >= 1,
    ])
    if drift_flags >= 1:
        return _build("drifting", 60, cycles, insight_category, repetition=False)

    # ── aligned ─────────────────────────────────────────────────────────────────
    return _build("aligned", 55, cycles, insight_category, repetition=False)


def _build(
    state: str,
    confidence: int,
    cycles: int,
    category: str,
    repetition: bool,
) -> StrategicMemoryDrift:
    rep_note: Optional[str] = None
    if repetition and state in ("compounding_repetition", "fragmented"):
        rep_note = _REPETITION_PATTERNS.get(category, _DEFAULT_REPETITION_PATTERN)
    return StrategicMemoryDrift(
        drift_state=state,
        memory_continuity=_CONTINUITY[state],
        doctrine_alignment_note=_DOCTRINE_NOTES[state],
        repetition_pattern_note=rep_note,
        drift_note=_DRIFT_NOTES[state],
        drift_confidence=confidence,
        historical_cycles=cycles,
    )
