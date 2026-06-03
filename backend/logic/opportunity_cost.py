"""
Opportunity Cost Intelligence — Sprint 45.

Explains how the cost of future decisions changes over time.
NOT urgency. NOT pressure tactics.
Economics layer: what becomes harder, what decisions get more expensive,
where reversibility window gradually narrows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class OpportunityCost:
    future_intervention_cost: str           # minimal | moderate | elevated | structural
    reversibility_shift_note: str           # state narrative shown in card footer
    opportunity_cost_note:    Optional[str] # broader narrative shown in card body
    dependency_note:          Optional[str] # "Вероятно затронет: X" — only if applicable


# ── Operational band mapping ──────────────────────────────────────────────────
_OC_STATE_COST: dict[str, str] = {
    "contained":              "minimal",
    "compounding":            "moderate",
    "narrowing_window":       "elevated",
    "structurally_expensive": "structural",
}

# ── Reversibility shift narratives ────────────────────────────────────────────
_REVERSIBILITY_NOTES: dict[str, str] = {
    "contained":              "Сценарий остаётся в основном обратимым",
    "compounding":            "Часть решений может потребовать более длительной стабилизации",
    "narrowing_window":       "Возможность мягкой стабилизации постепенно сокращается",
    "structurally_expensive": "Часть изменений может потребовать структурной перестройки",
}

# ── Opportunity cost notes by state ──────────────────────────────────────────
_OC_NOTES: dict[str, Optional[str]] = {
    "contained":              None,
    "compounding":            "Поздняя стабилизация обычно требует более длительного операционного окна.",
    "narrowing_window":       "Поздняя стабилизация, вероятно, потребует структурного пересмотра операционной модели.",
    "structurally_expensive": "Поздняя стабилизация, вероятно, потребует структурного пересмотра экономики или операционной модели.",
}

# ── Category → dependency domain (shown only for elevated/structural states) ─
_DEPENDENCY_MAP: dict[str, str] = {
    "margin_crisis":          "закупочную модель",
    "high_ad_spend":          "рекламный бюджет",
    "price_pressure_cluster": "ценовую модель",
    "low_stock":              "складскую логистику",
}


def _derive_oc_state(
    counterfactual_pressure_state: Optional[str],
    reversibility_state:           Optional[str],
    pressure_accumulation:         Optional[str],
    trajectory_state:              Optional[str],
) -> str:
    """Derive the 4-band opportunity cost state from existing enriched signal fields."""
    # structurally_expensive — highest severity
    if (
        reversibility_state == "structurally_locked"
        or trajectory_state == "structurally_accumulating"
        or counterfactual_pressure_state == "structurally_locked"
    ):
        return "structurally_expensive"

    # narrowing_window
    if (
        reversibility_state == "narrowing_window"
        or counterfactual_pressure_state == "accelerating"
        or (
            pressure_accumulation == "compounding"
            and trajectory_state in ("escalating", "persistent")
        )
    ):
        return "narrowing_window"

    # compounding
    if (
        pressure_accumulation == "compounding"
        or counterfactual_pressure_state == "narrowing"
        or (
            pressure_accumulation == "accumulating"
            and trajectory_state == "persistent"
        )
    ):
        return "compounding"

    return "contained"


def compute_opportunity_cost(
    insight:                        Any,
    counterfactual_pressure_state:  Optional[str],
    reversibility_state:            Optional[str],
    pressure_accumulation:          Optional[str],
    trajectory_state:               Optional[str],
    forecast_instability_window_days: Optional[int],
) -> OpportunityCost:
    """
    Compute opportunity cost intelligence for a single insight.
    Must run after counterfactual, trajectory, and forecast enrichment.
    NEVER modifies confidence scores or intervention_tier.
    """
    oc_state = _derive_oc_state(
        counterfactual_pressure_state=counterfactual_pressure_state,
        reversibility_state=reversibility_state,
        pressure_accumulation=pressure_accumulation,
        trajectory_state=trajectory_state,
    )

    cat     = getattr(insight, "key", "").split(":")[0]
    dep_str = _DEPENDENCY_MAP.get(cat) if oc_state in ("narrowing_window", "structurally_expensive") else None
    dep_note = f"Вероятно затронет: {dep_str}" if dep_str else None

    return OpportunityCost(
        future_intervention_cost=_OC_STATE_COST[oc_state],
        reversibility_shift_note=_REVERSIBILITY_NOTES[oc_state],
        opportunity_cost_note=_OC_NOTES[oc_state],
        dependency_note=dep_note,
    )
