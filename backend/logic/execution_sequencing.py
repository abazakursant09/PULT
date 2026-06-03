"""
Execution Sequencing — Sprint 32.

Builds stabilization order based on operational leverage, reversibility,
stabilization latency, and pressure dependencies.

NOT a task planner. NOT sorted by severity.
Operational intervention sequencing with dependency awareness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class SequencedAction:
    insight_key:                        str
    sequence_stage:                     int
    sequence_priority:                  int
    stabilization_role:                 str   # fast_stabilization | structural_fix | parallel_track | isolated
    expected_stabilization_window_days: int
    unlocks_next_stage:                 bool
    dependency_reduction:               list[str]
    sequencing_confidence:              str   # low | moderate | stable | high
    sequencing_note:                    str
    insight_title:                      str   # display title from InsightItem
    insight_product:                    Optional[str] = None


# ── Type config ───────────────────────────────────────────────────────────────
# Each insight type has a default sequencing profile.
# Stage can be adjusted dynamically based on co-occurring signals.

_TYPE_CONFIG: dict[str, dict] = {
    "high_ad_spend": {
        "base_stage":            1,
        "role":                  "fast_stabilization",
        "window_days":           14,
        "unlocks_next":          True,
        "reduces":               ["давление на маржу", "зависимость от рекламы", "операционный ДРР"],
        "friction":              "low",
        "reversibility":         "high",
        "base_confidence":       "stable",
    },
    "low_stock": {
        "base_stage":            1,
        "role":                  "fast_stabilization",
        "window_days":           7,
        "unlocks_next":          True,
        "reduces":               ["давление на видимость", "волатильность позиций"],
        "friction":              "low",
        "reversibility":         "high",
        "base_confidence":       "stable",
    },
    "margin_crisis": {
        "base_stage":            1,   # elevated to 2 if high_ad_spend co-occurs
        "role":                  "structural_fix",
        "window_days":           21,
        "unlocks_next":          False,
        "reduces":               ["операционный дрейф", "системное давление портфеля"],
        "friction":              "high",
        "reversibility":         "low",
        "base_confidence":       "moderate",
    },
    "seo_opportunity": {
        "base_stage":            1,   # elevated to 2 if stock or margin issues co-occur
        "role":                  "parallel_track",
        "window_days":           21,
        "unlocks_next":          False,
        "reduces":               [],
        "friction":              "medium",
        "reversibility":         "medium",
        "base_confidence":       "moderate",
    },
    "high_rating": {
        "base_stage":            2,
        "role":                  "parallel_track",
        "window_days":           21,
        "unlocks_next":          False,
        "reduces":               [],
        "friction":              "low",
        "reversibility":         "high",
        "base_confidence":       "low",
    },
}

# Insight types excluded from stabilization sequencing (positive signals)
_EXCLUDED_FROM_SEQUENCING = {"sales_growth"}

# Human-readable role labels
_ROLE_LABELS: dict[str, str] = {
    "fast_stabilization": "быстрый эффект стабилизации",
    "structural_fix":     "структурное изменение",
    "parallel_track":     "параллельный трек",
    "isolated":           "изолированный сигнал",
}

# Sequencing note templates
_NOTE_STAGE1_FAST    = "Обычно выполняется на раннем этапе стабилизации."
_NOTE_STAGE1_UNLOCK  = "Стабилизация здесь может снизить давление на следующем этапе."
_NOTE_STAGE2_AFTER   = "Имеет смысл после снижения операционной волатильности."
_NOTE_STAGE2_PARALLEL = "Может быть реализовано параллельно с основной стабилизацией."
_NOTE_STRUCTURAL     = "Структурное изменение — рекомендуется после стабилизации оперативных показателей."
_NOTE_PARALYSIS      = (
    "Система рекомендует поэтапную стабилизацию, "
    "а не одновременное исправление всех отклонений."
)


def _cat(key: str) -> str:
    return key.split(":")[0]


def _is_high_friction(insight) -> bool:
    cat = _cat(getattr(insight, "key", ""))
    cfg = _TYPE_CONFIG.get(cat, {})
    return cfg.get("friction") == "high"


def build_execution_sequence(
    insights:           list,
    portfolio_patterns: list,
    operational_focus:  Any,
    fatigue_score:      float = 0.0,
) -> list[SequencedAction]:
    """
    Build stabilization sequence from current insight state.

    Sequencing logic:
    1. Reversibility — prefer reversible interventions first
    2. Stabilization speed — fast-acting before slow-acting
    3. Dependency reduction — upstream causes before downstream effects
    4. Friction score — low-friction before high-friction
    5. Pressure cascade — high_ad_spend before margin_crisis
    6. Structural coupling — if A unlocks B, do A first

    Returns ordered list[SequencedAction], stage ascending, priority ascending.
    Excludes positive signals (sales_growth).
    """
    # Only warning/info insights that are active
    active_warnings = [
        i for i in insights
        if getattr(i, "type", "") in ("warning", "info")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if not active_warnings:
        return []

    # Collect insight type categories present
    present_cats = {_cat(getattr(i, "key", "")) for i in active_warnings}

    # Paralysis detection: 3+ high-friction AND fatigue > 0.6
    high_friction_count = sum(1 for i in active_warnings if _is_high_friction(i))
    paralysis_risk = high_friction_count >= 3 and fatigue_score > 0.6

    # Check for recurring lifecycle
    has_recurring = any(
        getattr(i, "signal_lifecycle_stage", None) == "recurring"
        for i in active_warnings
    )

    sequence: list[SequencedAction] = []
    seen_categories: set[str] = set()
    priority_counter = 0

    for ins in active_warnings:
        cat = _cat(getattr(ins, "key", ""))

        # Skip excluded types and already-seen categories
        if cat in _EXCLUDED_FROM_SEQUENCING:
            continue
        if cat not in _TYPE_CONFIG:
            continue
        if cat in seen_categories:
            continue
        seen_categories.add(cat)

        cfg = _TYPE_CONFIG[cat]
        title = getattr(ins, "title", cat)
        product = getattr(ins, "product_name", None)

        # Dynamic stage adjustment based on co-occurring signals
        stage = cfg["base_stage"]
        if cat == "margin_crisis" and "high_ad_spend" in present_cats:
            # Ad stabilization may reduce margin pressure first
            stage = 2
        if cat == "seo_opportunity" and ("low_stock" in present_cats or "margin_crisis" in present_cats):
            # Recover ops stability before SEO work
            stage = 2

        # Under paralysis risk, compress stage 3+ signals to stage 2
        if paralysis_risk and stage > 2:
            stage = 2

        # Determine note
        role = cfg["role"]
        unlocks = cfg["unlocks_next"]
        reduces = list(cfg["reduces"])

        if stage == 1 and unlocks:
            note = _NOTE_STAGE1_UNLOCK
        elif stage == 1:
            note = _NOTE_STAGE1_FAST
        elif role == "structural_fix":
            if cat == "margin_crisis" and "high_ad_spend" in present_cats:
                note = "Имеет смысл после стабилизации рекламной нагрузки."
            else:
                note = _NOTE_STRUCTURAL
        elif role == "parallel_track":
            note = _NOTE_STAGE2_PARALLEL
        else:
            note = _NOTE_STAGE2_AFTER

        # Sequencing confidence
        conf = cfg["base_confidence"]
        if has_recurring and cat in ("margin_crisis", "high_ad_spend"):
            conf = "stable"
        if paralysis_risk:
            conf = "moderate"

        priority_counter += 1
        sequence.append(SequencedAction(
            insight_key=getattr(ins, "key", cat),
            sequence_stage=stage,
            sequence_priority=priority_counter,
            stabilization_role=role,
            expected_stabilization_window_days=cfg["window_days"],
            unlocks_next_stage=unlocks,
            dependency_reduction=reduces,
            sequencing_confidence=conf,
            sequencing_note=note,
            insight_title=title,
            insight_product=product,
        ))

    if not sequence:
        return []

    # Sort: stage asc, then by priority within stage
    sequence.sort(key=lambda s: (s.sequence_stage, s.sequence_priority))

    # Re-assign priority numbers after sort
    for idx, s in enumerate(sequence):
        s.sequence_priority = idx + 1

    return sequence


def sequencing_summary_line(sequence: list[SequencedAction]) -> Optional[str]:
    """
    Build a single narrative line for OperationalSummary.
    Only emitted when sequence depth >= 2 and dependency chains exist.
    """
    if len(sequence) < 2:
        return None

    # Check if any stage 1 action unlocks stage 2
    stage1 = [s for s in sequence if s.sequence_stage == 1 and s.unlocks_next_stage]
    stage2 = [s for s in sequence if s.sequence_stage == 2]

    if stage1 and stage2:
        return (
            "Часть отклонений связана между собой — "
            "локальная стабилизация может снизить downstream pressure."
        )
    return None


def role_label(role: str) -> str:
    """Human-readable display label for stabilization_role."""
    return _ROLE_LABELS.get(role, role)
