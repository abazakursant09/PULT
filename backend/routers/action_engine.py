"""
Action Engine — /api/insights
Rule-based operational intelligence. No fake ML — pure heuristics on real data.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user

from models.user import User
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.seo_rebuild import SeoRebuild
from data.marketplace_mechanics import get_mechanic
from services.insight_keys import build_insight_key
from services.insight_decision_bridge import (
    promote_insight_to_decision as _promote_decision,
    InsightPromotionDTO as _PromotionDTO,
)
from services.execution_measurement_bridge import (
    open_measurement_for_execution as _open_measurement,
)
from logic.marketplace_behavior import behavior_note_for_insight as _mp_behavior
from logic.outcome_memory import (
    evaluate_resolution_outcome as _eval_outcome,
    build_outcome_note as _build_outcome_note,
    apply_outcome_to_recommendations as _apply_outcome_recs,
)

router = APIRouter()


# ── Category benchmarks ────────────────────────────────────────────────────────

BENCHMARKS: dict[str, dict] = {
    "wildberries": {
        "margin_median":         0.22,
        "ad_spend_ratio_median": 0.12,
        "revenue_per_ad":        8.5,
        "rating_good":           4.5,
        "label":                 "Wildberries",
    },
    "ozon": {
        "margin_median":         0.20,
        "ad_spend_ratio_median": 0.10,
        "revenue_per_ad":        9.5,
        "rating_good":           4.4,
        "label":                 "Ozon",
    },
    "yandex_market": {
        "margin_median":         0.18,
        "ad_spend_ratio_median": 0.09,
        "revenue_per_ad":        10.0,
        "rating_good":           4.3,
        "label":                 "Яндекс Маркет",
    },
}
_DEFAULT_BM = BENCHMARKS["wildberries"]


def _bm(mp: str) -> dict:
    return BENCHMARKS.get(mp, _DEFAULT_BM)


def _mp_label(mp: str) -> str:
    return BENCHMARKS.get(mp, {}).get("label", mp.replace("_", " ").title())


# ── Schemas ────────────────────────────────────────────────────────────────────

class InsightDebug(BaseModel):
    """Dev-only ranking explainability. Never populated in production."""
    preference_modifier:    float  # net behavioral score modifier applied to ranking
    memory_decay:           float  # recency factor [0–1]; 0 = no signals, 1 = all recent
    resurfaced_contextually: bool  # True if positive preference helped this insight surface


class InsightImpact(BaseModel):
    label:    str
    estimate: str
    sign:     Literal["negative", "positive", "neutral"]


class InsightBenchmark(BaseModel):
    metric:    str
    value:     str
    baseline:  str
    deviation: str


class InsightAction(BaseModel):
    label:  str
    url:    str
    params: Optional[dict] = None
    type:   Literal["primary", "secondary"]


class StyleRecommendation(BaseModel):
    style_name:        str
    win_rate:          int
    avg_ctr_uplift:    float
    sample_size:       int
    best_categories:   list[str]
    best_marketplaces: list[str]
    explanation_lines: list[str]
    is_sufficient:     bool


class InsightItem(BaseModel):
    id:               str
    key:              str
    type:             Literal["warning", "positive", "info"]
    icon:             str
    title:            str
    subtitle:         Optional[str]
    reasons:          list[str]
    recommendations:  list[str]
    confidence:       int
    confidence_level: Literal["low", "medium", "high"]
    impact:           Optional[InsightImpact]
    benchmark:        Optional[InsightBenchmark]
    actions:          list[InsightAction]
    status:           str
    record_id:        Optional[str]
    product_name:     Optional[str]
    product_sku:      Optional[str]
    marketplace:      Optional[str]
    is_demo:          bool
    # Money impact (structured, Stage 27)
    impact_score:                 Optional[int]   = None   # 0-100
    estimated_monthly_loss_rub:   Optional[float] = None
    estimated_monthly_gain_rub:   Optional[float] = None
    # A/B style recommendation (Stage 31)
    style_recommendation: Optional[StyleRecommendation] = None
    # Dev-only explainability — None in production
    debug:                Optional[InsightDebug]         = None
    # Marketplace operational memory (Part 9)
    automation_level:     Optional[str]                  = None  # AutomationLevel literal
    marketplace_mechanic: Optional[str]                  = None  # KB slug
    marketplace_risk_note: Optional[str]                 = None  # shown in signal card
    # Historical memory (Part 10) — what PULT has seen before for this product
    memory_context:       Optional[str]                  = None  # past pattern note; None = first occurrence
    # Operational chains — causal relationship to another insight
    is_secondary: bool         = False  # True = this insight is a consequence, not a root cause
    chain_id:     Optional[str] = None  # ID of the OperationalChain this belongs to
    # Simulation meta — excluded from API, used internally for scenario generation
    sim_meta: Optional[dict]   = Field(default=None, exclude=True)
    # Operator learning — adaptation note shown when recommendations were adjusted
    adaptation_note: Optional[str] = None
    # Trust calibration (Sprint 19) — decision weight + signal classification
    weight:                Optional[float]                                                   = None
    signal_state:          Optional[Literal["temporary", "persistent", "structural"]]        = None
    resolution_difficulty: Optional[Literal["easy", "moderate", "hard"]]                    = None
    intervention_tier:     Optional[Literal["monitor", "background", "attention", "immediate"]] = None
    # Marketplace behavior memory (Sprint 20) — platform mechanics context
    marketplace_patterns:             list[str]      = []
    marketplace_behavior_note:        Optional[str]  = None
    marketplace_stabilization_window: Optional[int]  = None
    # Retrospective outcome memory (Sprint 21) — what happened after prior interventions
    outcome_memory_note:  Optional[str] = None
    outcome_state:        Optional[str] = None  # improved | stabilized | temporary | failed | repeated
    outcome_confidence:   Optional[int] = None
    # Decision confidence (Sprint 23) — operational certainty across all signals
    decision_confidence_score:  Optional[int] = None  # 0–100
    decision_confidence_band:   Optional[str] = None  # low | moderate | stable | high
    decision_confidence_reason: Optional[str] = None
    decision_stability_note:    Optional[str] = None
    # Signal lifecycle (Sprint 24) — operational phase of this signal
    signal_lifecycle_stage:  Optional[str] = None  # emerging | confirmed | stabilized | recurring | resolved
    signal_lifecycle_note:   Optional[str] = None
    signal_lifecycle_weight: Optional[int] = None  # 5 | 15 | 20 | 55 | 85
    signal_operational_age:  Optional[int] = None  # days since first seen
    signal_recurrence_count: Optional[int] = None
    # Outcome feedback (Sprint 26) — evidence of intervention effectiveness
    outcome_feedback_note:            Optional[str]  = None  # narrative shown below recommendations
    recommendation_confidence_delta:  Optional[int]  = None  # +10 | -6 | -12 | 0
    recommended_based_on_history:     Optional[bool] = None  # True = reinforce bias
    # Signal age decay (Sprint 27) — temporal freshness of operational evidence
    signal_decay_state:   Optional[str]   = None  # fresh | aging | fading | stale | persistent
    signal_decay_penalty: Optional[int]   = None  # confidence penalty already applied to score
    signal_decay_note:    Optional[str]   = None  # shown in expanded section
    signal_age_days:      Optional[int]   = None  # total age in days
    # Execution sequencing (Sprint 32) — stabilization order
    sequence_stage:                      Optional[int]  = None  # 1 | 2 | 3
    stabilization_role:                  Optional[str]  = None  # fast_stabilization | structural_fix | parallel_track
    expected_stabilization_window_days:  Optional[int]  = None
    unlocks_next_stage:                  Optional[bool] = None
    # Operational trajectory (Sprint 33) — pressure direction and reversibility
    trajectory_state:           Optional[str] = None  # reversible | stabilizing | persistent | escalating | structurally_accumulating
    trajectory_direction:       Optional[str] = None  # improving | stable | worsening | critical
    reversibility_state:        Optional[str] = None  # easily_reversible | conditionally_reversible | narrowing_window | structurally_locked
    stabilization_window_days:  Optional[int] = None  # approx soft-stabilization horizon; None = high uncertainty
    pressure_accumulation:      Optional[str] = None  # dissipating | stable | accumulating | compounding
    trajectory_note:            Optional[str] = None  # one-sentence restrained narrative
    # Operational tradeoff (Sprint 34) — secondary consequences of intervention
    tradeoff_note:              Optional[str] = None  # what temporarily arises after stabilization
    tradeoff_severity:          Optional[str] = None  # mild | moderate | significant
    tradeoff_duration_days:     Optional[int] = None  # approximate secondary-effect duration
    reversibility_profile:      Optional[str] = None  # reversible | conditionally_reversible | monitor_required
    stabilization_benefit:      Optional[str] = None  # primary gain from intervention
    # Operational failure forecast (Sprint 35) — foresight layer
    forecast_escalation_probability:  Optional[int] = None  # 0-100
    forecast_fragility_state:         Optional[str] = None  # stable | sensitive | fragile | critical
    forecast_next_stage:              Optional[str] = None  # probable next operational phase
    forecast_first_failure_mode:      Optional[str] = None  # what breaks first if pressure persists
    forecast_note:                    Optional[str] = None  # restrained narrative
    forecast_instability_window_days: Optional[int] = None  # approximate horizon; None = high uncertainty
    # Operational recovery paths (Sprint 36) — recovery intelligence
    recovery_probability:          Optional[int] = None  # 0-100
    recovery_state:                Optional[str] = None  # quick | gradual | structural | unstable
    first_recovered_metric:        Optional[str] = None  # what normalizes fastest
    lagging_metric:                Optional[str] = None  # what stays unstable longest
    expected_recovery_window_days: Optional[int] = None  # approximate; None = structural uncertainty
    recovery_note:                 Optional[str] = None  # restrained narrative
    recovery_dependency:           Optional[str] = None  # precondition for recovery
    # Stabilization lock (Sprint 38) — observation window pacing
    recovery_signal_state:                Optional[str] = None  # waiting | stabilizing | reopening | ready
    lock_estimated_recovery_window_days:  Optional[int] = None  # days until clean attribution
    lock_reentry_condition:               Optional[str] = None  # signal to wait for
    lock_next_safe_action:                Optional[str] = None  # first safe action after window
    # Counterfactual pressure (Sprint 39) — inaction cost + timing intelligence
    counterfactual_pressure_state:              Optional[str] = None  # stable | narrowing | accelerating | structurally_locked
    counterfactual_transition_window_days:      Optional[int] = None  # typical phase-transition horizon
    counterfactual_reversibility_remaining_pct: Optional[int] = None  # approximate flexibility remaining
    counterfactual_next_phase:                  Optional[str] = None  # likely next instability phase
    counterfactual_operational_time_pressure:   Optional[str] = None  # low | moderate | elevated | critical
    counterfactual_note:                        Optional[str] = None  # restrained narrative
    # Comparative simulation (Sprint 42) — two-path operational comparison
    path_comparison: Optional["PathComparisonOut"] = None
    # Observability recovery forecast (Sprint 44) — when signal becomes interpretable again
    obs_recovery_state:      Optional[str] = None  # clear | recovering | distorted | fragmented | reset_required
    obs_recovery_window_days: Optional[int] = None  # estimated days to clean attribution
    obs_recovery_condition:  Optional[str] = None  # what must happen for recovery
    obs_blocking_factor:     Optional[str] = None  # what currently prevents interpretation
    obs_recovery_note:       Optional[str] = None  # restrained 1-sentence narrative
    # Adaptive intervention timing (Sprint 48) — when to intervene
    timing_state:                Optional[str] = None  # observation_phase | stabilization_phase | emerging_window | narrowing_window | immediate | structurally_late | optimal
    intervention_readiness:      Optional[str] = None  # ready | nearly_ready | unstable | elevated | late | monitor
    timing_note:                 Optional[str] = None  # restrained narrative
    optimal_window_days:         Optional[int] = None  # approximate timing horizon; displayed as label, not raw number
    premature_intervention_risk: Optional[str] = None  # low | moderate | high
    premature_risk_note:         Optional[str] = None
    delayed_intervention_risk:   Optional[str] = None  # low | moderate | high | structural
    delayed_risk_note:           Optional[str] = None
    waiting_benefit:             Optional[str] = None  # shown for observation_phase only
    readiness_condition:         Optional[str] = None  # prerequisite for safe intervention
    # Intervention reversal intelligence (Sprint 49) — diminishing returns + rollback economics
    reversal_state:               Optional[str] = None  # stable_intervention | diminishing_return | overextended | reversal_window | structurally_locked
    reversal_probability:         Optional[int] = None  # 0–100; used for visibility logic only, never shown as score
    reversal_window_days:         Optional[int] = None  # approximate; displayed as label
    reversal_trigger:             Optional[str] = None  # what is driving the reversal signal
    reversal_note:                Optional[str] = None  # restrained narrative
    rollback_safety:              Optional[str] = None  # safe | conditional | risky | blocked
    rollback_effect_expectation:  Optional[str] = None
    stabilization_dependency:     Optional[str] = None
    # Opportunity cost intelligence (Sprint 45) — economics of delayed decisions
    future_intervention_cost: Optional[str] = None  # minimal | moderate | elevated | structural
    reversibility_shift_note: Optional[str] = None  # state narrative shown in card footer
    opportunity_cost_note:    Optional[str] = None  # broader narrative shown in card body
    dependency_note:          Optional[str] = None  # "Вероятно затронет: X" — only if applicable
    # Secondary pressure cascade (Sprint 50) — pressure propagation into adjacent operational zones
    cascade_state:             Optional[str] = None  # isolated | shifting_pressure | coupled_instability | structurally_cascading
    cascade_direction:         Optional[str] = None  # localized | adjacent | expanding | systemic
    secondary_pressure_target: Optional[str] = None  # what operational zone is under secondary pressure
    cascade_probability:       Optional[int] = None  # 0–100; propagation probability
    cascade_window_days:       Optional[int] = None  # approximate onset horizon; None for isolated
    cascade_note:              Optional[str] = None  # restrained narrative
    cascade_offset_note:       Optional[str] = None  # timing offset narrative
    # Resilience snapshot (Sprint 51) — point-in-time operational shock absorption capacity
    resilience_state:          Optional[str] = None  # adaptive | resilient | moderate | narrowing | brittle | collapsing | exhausted
    absorption_capacity:       Optional[str] = None  # high | moderate | narrowing | exhausted
    weakest_operational_layer: Optional[str] = None  # most vulnerable operational zone
    resilience_window:         Optional[int] = None  # approximate days until state shift; None = stable/exhausted
    resilience_score:          Optional[int] = None  # 0–100; internal composite; not displayed
    resilience_note:           Optional[str] = None  # restrained narrative
    # Resilience trajectory (Sprint 52) — how operational elasticity evolves over time
    resilience_trajectory:            Optional[str] = None  # recovering | stabilizing | degrading | structurally_degrading
    resilience_trajectory_velocity:   Optional[str] = None  # gradual | accelerating (degrading states only)
    resilience_trajectory_note:       Optional[str] = None
    absorption_transition_note:       Optional[str] = None  # inferred recent absorption capacity movement
    resilience_trajectory_confidence: Optional[int] = None  # 0–100; used for Telegram gating
    # Adaptive capacity intelligence (Sprint 53) — direction of operational adaptation over cycles
    adaptive_capacity_state: Optional[str] = None  # strengthening | adaptive | plateauing | rigid | deteriorating
    adaptation_direction:    Optional[str] = None  # improving | stable | plateauing | constrained | declining
    stabilization_trend:     Optional[str] = None  # direction of stabilization window length across cycles
    observability_trend:     Optional[str] = None  # direction of observability quality across cycles
    recurrence_trend:        Optional[str] = None  # direction of recurrence burden across cycles
    adaptation_note:         Optional[str] = None
    adaptation_confidence:   Optional[int] = None  # 0–100; used for Telegram gating
    adaptation_cycles:       Optional[int] = None  # estimated cycles observed (heuristic)
    # Strategic memory drift (Sprint 54) — divergence from historically effective recovery doctrine
    strategic_drift_state:   Optional[str] = None  # aligned | drifting | fragmented | historically_disconnected | compounding_repetition
    memory_continuity:       Optional[str] = None  # connected | partially_connected | fragmented | disconnected
    doctrine_alignment_note: Optional[str] = None  # brief historical continuity note
    repetition_pattern_note: Optional[str] = None  # what pattern is repeating (fragmented/compounding only)
    drift_note:              Optional[str] = None  # restrained narrative
    drift_confidence:        Optional[int] = None  # 0–100; used for Telegram gating
    historical_cycles:       Optional[int] = None  # estimated historical reference cycles


class OperationalChain(BaseModel):
    id:                      str
    type:                    Literal["degradation", "recovery"]
    root_insight_key:        str
    consequence_insight_key: str
    root_title:              str
    consequence_title:       str
    chain_text:              str          # one-line causal description
    evidence:                list[str]    # 2-3 evidence lines
    confidence:              int
    confidence_level:        Literal["low", "medium", "high"]
    product_name:            Optional[str]
    marketplace:             Optional[str]


class OperationalScenario(BaseModel):
    scenario_id:       str
    source_insight:    str        # insight key this scenario belongs to
    scenario_type:     str        # reduce_ads | increase_discount | etc.
    path_type:         Literal["conservative", "balanced", "aggressive"]
    assumption:        str        # what the scenario assumes
    expected_effect:   str        # likely operational outcome
    tradeoff:          str        # what's at risk
    risk_level:        Literal["low", "medium", "high"]
    confidence:        int
    confidence_level:  Literal["low", "medium", "high"]
    time_horizon_days: int
    reversible:        bool
    causal_chain:      list[str]  # readable chain steps
    evidence_basis:    str
    uncertainty_note:  str        # always present — epistemic humility


class FocusBlock(BaseModel):
    focus_id:         str
    title:            str
    reason:           str
    root_cause:       str
    expected_impact:  str
    time_sensitivity: Literal["immediate", "this_week", "this_month"]
    confidence:       int
    is_stable:        bool
    linked_signals:   list[str] = []
    linked_scenarios: list[str] = []
    linked_chains:    list[str] = []
    primary_action:   str = ""
    secondary_action: Optional[str] = None
    # Sprint 30: temporal momentum
    focus_momentum:   Optional[str] = None   # active | slowing | historical | persistent
    effective_weight: Optional[int] = None


class PortfolioPattern(BaseModel):
    id:                      str
    pattern_type:            str
    marketplace:             Optional[str]
    category:                Optional[str]
    affected_products:       list[str]
    insight_types:           list[str]
    operational_summary:     str
    systemic_risk:           str
    confidence:              int
    stabilization_complexity: Literal["localized", "moderate", "systemic"]
    recommendation_bias:     Optional[str] = None
    # Sprint 28: root cause hypothesis
    root_cause_hypothesis:   Optional[str] = None
    root_cause_note:         Optional[str] = None
    root_cause_confidence:   Optional[int] = None
    root_cause_band:         Optional[str] = None
    # Sprint 28: historical memory
    cross_mp_memory_note:    Optional[str] = None
    cross_mp_stability_days: Optional[int] = None


class OperationalSummaryOut(BaseModel):
    summary_type:          str
    operational_shift:     str
    dominant_pressure:     Optional[str]
    improving_systems:     list[str]
    destabilizing_systems: list[str]
    recurring_patterns:    list[str]
    stabilized_patterns:   list[str]
    portfolio_direction:   str  # stabilizing | unstable | mixed | expanding_pressure
    operator_load:         str  # low | moderate | high
    summary_note:          str
    narrative_lines:       list[str]  # pre-built display lines, max 4
    outcome_feedback_line:  Optional[str] = None  # Sprint 26: feedback evidence summary
    decay_summary_line:     Optional[str] = None  # Sprint 27: stale/persistent narrative
    momentum_summary_line:  Optional[str] = None  # Sprint 30: temporal momentum narrative
    sequencing_summary_line:  Optional[str] = None  # Sprint 32: dependency chain narrative
    trajectory_summary_line:  Optional[str] = None  # Sprint 33: pressure accumulation narrative


class SequencedActionOut(BaseModel):
    insight_key:                        str
    sequence_stage:                     int
    sequence_priority:                  int
    stabilization_role:                 str
    expected_stabilization_window_days: int
    unlocks_next_stage:                 bool
    dependency_reduction:               list[str]
    sequencing_confidence:              str
    sequencing_note:                    str
    insight_title:                      str
    insight_product:                    Optional[str] = None


class OperationalCapacityOut(BaseModel):
    capacity_state:              str          # stable | loaded | saturated | overloaded
    operational_bandwidth_score: int          # 0-100
    overload_risk:               str          # low | moderate | high | critical
    defer_categories:            list[str]    # categories to temporarily defer
    capacity_note:               Optional[str] = None


class OperatorStrategyProfileOut(BaseModel):
    intervention_style:            str   # stable | reactive | aggressive | delayed | oscillating
    pacing_discipline:             str   # strong | moderate | weak
    recovery_patience:             str   # patient | unstable | intervention_prone
    structural_decision_tendency:  str   # balanced | symptom_focused | structurally_avoidant
    operational_volatility_source: str   # market_driven | mixed | operator_driven
    strategic_stability_score:     int   # 0-100
    stability_band:                str   # unstable | elevated | generally_stable | disciplined
    coaching_note:                 Optional[str] = None
    profile_confidence:            str   # low | moderate | stable | high


class ComparativePathOut(BaseModel):
    action_type:          str
    stabilization_speed:  str   # faster | moderate | slower
    volatility_impact:    str   # lower | moderate | higher
    observability_impact: str   # preserved | reduced | unclear
    operator_load:        str   # lower | moderate | higher
    reversibility_profile: str  # stronger | neutral | weaker
    structural_depth:     str   # tactical | mixed | structural
    path_note:            str


class PathComparisonOut(BaseModel):
    insight_key:          str
    path_a:               ComparativePathOut
    path_b:               ComparativePathOut
    contextual_note:      str
    comparison_dimension: str   # volatility | reversibility | speed | observability | load


class StrategyShiftOut(BaseModel):
    previous_strategy: str
    current_strategy:  str
    shift_type:        str   # escalation | fragmentation | structural_shift | tactical_switch
    shift_note:        Optional[str] = None


class StrategyCommitmentOut(BaseModel):
    strategy_type:                   str
    commitment_state:                str   # emerging | active | stabilizing | fragmented | abandoned
    interruption_risk:               str   # low | moderate | high
    observability_quality:           str   # clear | sufficient | degraded | unclear
    commitment_score:                Optional[int]  = None
    commitment_note:                 Optional[str]  = None
    estimated_observation_window_days: Optional[int] = None
    strategy_shift:                  Optional[StrategyShiftOut] = None


class DecisionDriftOut(BaseModel):
    drift_state:             str  # stable_execution | reactive_switching | fragmented_recovery | oscillating_pressure | stabilization_breakdown
    drift_note:              str  # restrained narrative
    intervention_overlap:    str  # none | low | moderate | high
    sequencing_continuity:   str  # stable | partial | fragmented | broken
    observation_reset_count: int  # signals currently in reset/reopening state


class OperationalRegimeOut(BaseModel):
    regime:                 str  # expansion | stabilization | defensive | constrained | containment | recovery_transition
    regime_direction:       str  # stabilizing | deteriorating | recovering | structurally_accumulating | constrained
    operational_posture:    str  # expansion_tolerant | equilibrium_focused | preservation_oriented | flexibility_constrained | deterioration_containment | recovery_rebuilding
    resilience_context:     str
    intervention_tolerance: str  # high | moderate | selective | narrow | minimal
    observability_quality:  str  # strong | moderate | degraded | fragmented
    regime_note:            str
    regime_confidence:      int


class DecisionEnergyOut(BaseModel):
    energy_state:         str   # lightweight | manageable | draining | disruptive | structurally_exhausting
    coordination_load:    str
    observability_load:   str
    stabilization_burden: str
    execution_complexity: str
    energy_note:          str
    energy_confidence:    int


class OperationalPhaseTransitionOut(BaseModel):
    phase:                str   # adaptive_equilibrium | stabilization_cycle | defensive_convergence | structural_pressure_formation | resilience_fragmentation | constrained_operation | recovery_reentry
    transition_direction: str   # stabilizing | restrictive | deteriorating | recovering
    transition_velocity:  str   # stable | gradual | accelerating
    transition_stability: str   # stable | moderate | unstable | fragmented
    transition_driver:    str
    phase_note:           str
    phase_confidence:     int


class StabilityTopologyOut(BaseModel):
    topology_state:           str   # balanced_stability | compensating_structure | narrowing_support | fragmented_stability | structurally_unbalanced | collapsing_compensation
    dominant_stability_layer: str
    weakest_stability_layer:  str
    compensation_behavior:    str
    structural_balance:       str
    remaining_flexibility:    str
    topology_note:            str
    topology_confidence:      int


class OperationalDoctrineOut(BaseModel):
    doctrine_state:             str   # adaptive_execution | recurring_operational_bias | defensive_patterning | stabilization_dependency | structurally_embedded_doctrine | rigid_operational_doctrine
    doctrine_pattern:           str
    adaptation_mode:            str
    institutionalization_level: str
    doctrine_flexibility:       str
    doctrine_note:              str
    doctrine_confidence:        int


class InstitutionalInertiaOut(BaseModel):
    inertia_state:            str   # flexible_structure | adaptive_inertia | operational_hardening | structural_inertia | locked_operational_behavior | institutional_freeze
    adaptation_resistance:    str
    behavioral_repeatability: str
    structural_elasticity:    str
    recovery_mobility:        str
    inertia_driver:           str
    inertia_window_days:      Optional[int]
    inertia_note:             str
    inertia_confidence:       int


class StructuralRecoveryCapacityOut(BaseModel):
    recovery_state:                 str   # structurally_recoverable | recoverable_with_adaptation | constrained_recovery | restructuring_dependent | continuity_without_recovery | structurally_exhausted
    structural_recoverability:      str
    recovery_elasticity:            str
    restructuring_requirement:      str
    continuity_dependence:          str
    structural_recovery_horizon:    str
    recovery_window_days:           Optional[int]
    structural_reversibility_index: float
    recovery_capacity_note:         str
    recovery_capacity_confidence:   int


class StructuralRecoveryPathTopologyOut(BaseModel):
    recovery_path_density:        float  # MIN state accessibility score along recovery path
    bottleneck_state:             str    # state with minimum accessibility
    min_accessibility_state:      str    # equals bottleneck_state
    dominant_path_type:           str    # direct_recovery_path | adaptive_recovery_path | restructuring_required_path | collapse_proximity_path | irreversible_path
    direct_recovery_path_count:   int
    adaptive_recovery_path_count: int
    blocked_edges_count:          int
    irreversible_edges_count:     int
    path_topology_note:           str
    path_topology_confidence:     int


def _clevel(conf: int) -> Literal["low", "medium", "high"]:
    if conf >= 75: return "high"
    if conf >= 55: return "medium"
    return "low"


def _fmt_rub(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def _fmt_k(amount: float) -> str:
    """Format as '≈ 62k ₽/мес' for display."""
    k = int(round(amount / 1000))
    return f"≈ {k}k ₽/мес" if k > 0 else f"≈ {int(amount)} ₽/мес"


def _impact_score(confidence: int, monthly_rub: float) -> int:
    """0-100 score: confidence 70% weight + magnitude 30% (calibrated at 200k₽/mo = 50 pts)."""
    magnitude = min(50, int(monthly_rub / 200_000 * 50))
    return min(100, int(confidence * 0.7) + magnitude)


def _extract_category(key: str) -> str:
    return key.split(":", 1)[0]


def _growth_maturity(daily: dict[str, float]) -> tuple[bool, int, int, float] | tuple[bool, int, int, float]:
    """
    Separate trends from spikes using 3-window confirmation.

    Returns (is_mature, periods_confirmed, growth_pct, cv):
      is_mature:        True only if growth confirmed across ≥2 independent windows
      periods_confirmed: 2 or 3 (number of confirmed rising windows)
      growth_pct:       % growth from oldest confirmed window to newest
      cv:               coefficient of variation in most recent window (0=flat, >0.6=spikey)

    Requires ≥6 days. 9+ days enables 3-window (higher confidence).
    """
    if len(daily) < 6:
        return False, 0, 0, 1.0

    dates = sorted(daily.keys())

    if len(dates) >= 9:
        # 3-window: oldest → middle → recent, each 3 days
        w1_vals = [daily[d] for d in dates[-9:-6]]
        w2_vals = [daily[d] for d in dates[-6:-3]]
        w3_vals = [daily[d] for d in dates[-3:]]
        w1, w2, w3 = sum(w1_vals), sum(w2_vals), sum(w3_vals)

        if w1 < 50:
            return False, 0, 0, 1.0

        rising_12 = w2 > w1 * 1.05
        rising_23 = w3 > w2 * 1.05
        periods = sum([rising_12, rising_23]) + 1  # base 1 + rising windows

        if not (rising_12 and rising_23):
            return False, 0, 0, 1.0

        mean_r = w3 / 3
        cv = (sum((v - mean_r) ** 2 for v in w3_vals) / 3) ** 0.5 / mean_r if mean_r > 0 else 1.0
        if cv > 0.6:
            return False, 0, 0, cv

        growth_pct = round((w3 - w1) / w1 * 100) if w1 > 0 else 0
        return True, 3, growth_pct, cv

    # 2-window fallback (6 days)
    w1_vals = [daily[d] for d in dates[-6:-3]]
    w2_vals = [daily[d] for d in dates[-3:]]
    w1, w2 = sum(w1_vals), sum(w2_vals)

    if w1 < 100:
        return False, 0, 0, 1.0

    if w2 <= w1 * 1.10:
        return False, 0, 0, 1.0

    mean_r = w2 / 3
    cv = (sum((v - mean_r) ** 2 for v in w2_vals) / 3) ** 0.5 / mean_r if mean_r > 0 else 1.0
    if cv > 0.6:
        return False, 0, 0, cv

    growth_pct = round((w2 - w1) / w1 * 100) if w1 > 0 else 0
    return True, 2, growth_pct, cv


def _ad_degradation_context(daily_rev: dict, days_active: int) -> tuple[bool, str, int]:
    """
    Returns (alert_warranted, context_note, confidence_penalty).

    Separates three scenarios:
      - Launch ramp-up  (< 7 days): suppress — insufficient data for judgment
      - Revenue scaling (growing rev alongside spend): suppress — intentional investment
      - Sustained degradation: alert warranted

    confidence_penalty: 0–15 pts subtracted when signal is young (< 15 days).
    """
    if days_active < 7:
        return False, f"Кампания активна {days_active} дн. — слишком мало данных для вывода", 0

    if days_active >= 10:
        dates   = sorted(daily_rev.keys())
        half    = len(dates) // 2
        rev_old = sum(daily_rev[d] for d in dates[:half]) or 0
        rev_new = sum(daily_rev[d] for d in dates[half:]) or 0
        if rev_old > 0 and rev_new > rev_old * 1.15:
            return (
                False,
                "Выручка растёт вместе с расходами — признак масштабирования, не деградации",
                0,
            )

    penalty = max(0, 15 - days_active)
    return True, f"Паттерн наблюдается {days_active} дн. — не разовый выброс кампании", penalty


def _margin_pressure_context(
    daily_rev:   dict,
    margin_pct:  float,
    ad_ratio:    float | None,
    commission:  float,
    logistics:   float,
    rev:         float,
    bm:          dict,
) -> tuple[bool, str, list[str], list[str], int]:
    """
    Returns (alert_warranted, pressure_source, context_reasons, recommendations, confidence_penalty).

    Distinguishes:
      - seasonal compression (< 7 days data) → suppress
      - strategic investment (growing rev + compressed margin) → warn, low urgency
      - structural deterioration (flat/declining rev + sustained margin loss) → full alert

    pressure_source: "ad_driven" | "logistics" | "commission" | "structural"
    confidence_penalty: 0–14 pts (seasonal compression uncertainty for young data)
    """
    days_active = len(daily_rev)

    if days_active < 7:
        return (
            False, "seasonal",
            [f"Данных {days_active} дн. — недостаточно для вывода о структуре затрат"],
            [], 0,
        )

    # ── Identify primary pressure source ──────────────────────────────────────
    pressure_source = "structural"
    source_reasons: list[str] = []
    source_recs:    list[str] = []

    comm_pct  = commission / rev if rev > 0 else 0
    logi_pct  = logistics  / rev if rev > 0 else 0
    ad_pct    = ad_ratio or 0

    if ad_ratio is not None and ad_pct > bm["ad_spend_ratio_median"] * 1.5:
        pressure_source = "ad_driven"
        source_reasons.append(
            f"Рекламные расходы ({ad_pct*100:.0f}% ДРР) — основной драйвер давления на маржу"
        )
        source_recs.append("Пересмотреть эффективность кампаний: снизить нецелевой трафик")
    elif comm_pct > 0.20:
        pressure_source = "commission"
        source_reasons.append(f"Комиссия площадки: {comm_pct*100:.0f}% от выручки")
        source_recs.append("Проверить тарифный план — комиссия превышает категорийную норму")
    elif logi_pct > 0.12:
        pressure_source = "logistics"
        source_reasons.append(f"Логистическая нагрузка: {logi_pct*100:.0f}% от выручки")
        source_recs.append("Оптимизировать упаковку и схему отгрузки для снижения логистики")
    else:
        source_reasons.append("Давление распределено по нескольким статьям затрат")
        source_recs.append("Провести постатейный анализ: реклама, логистика, комиссия, закупка")

    # ── Revenue trend → strategic vs deterioration ────────────────────────────
    trend_note = ""
    if days_active >= 10:
        dates   = sorted(daily_rev.keys())
        half    = len(dates) // 2
        rev_old = sum(daily_rev[d] for d in dates[:half]) or 0
        rev_new = sum(daily_rev[d] for d in dates[half:]) or 0
        if rev_old > 0:
            if rev_new > rev_old * 1.15:
                trend_note = "Выручка растёт — сжатие маржи может быть инвестиционным этапом"
            elif rev_new < rev_old * 0.90:
                trend_note = "Выручка снижается — маржа под двойным давлением"
            else:
                trend_note = f"Выручка стабильна {days_active} дн. — давление структурное"

    context_reasons = source_reasons[:]
    if trend_note:
        context_reasons.append(trend_note)

    recs = source_recs + [
        "Рассмотреть повышение цены на 5–10% с тестом конверсии",
        "Сравнить экономику с аналогами категории",
    ]

    penalty = max(0, 14 - days_active)
    return True, pressure_source, context_reasons, recs, penalty


_MARGIN_TITLES: dict[str, str] = {
    "ad_driven":  "Рекламные расходы опережают маржинальный потенциал товара",
    "logistics":  "Логистическая нагрузка сжимает операционную маржу",
    "commission": "Комиссия площадки снижает экономику товара",
    "structural": "Текущая структура затрат не компенсируется операционной динамикой",
}


def _build_memory_note(
    insight_key:      str,
    rule_category:    str,
    product_name:     str | None,
    resolved_history: dict[str, datetime],
    notif_counts:     dict[str, int],
    rebuild_outcomes: dict[str, "SeoRebuild"],
) -> str | None:
    """
    Returns a single-sentence memory note or None (first occurrence = no note).

    Sources:
      resolved_history  — InsightRecord.updated_at for resolved keys
      notif_counts      — TelegramNotificationLog count per insight_key (90-day window)
      rebuild_outcomes  — most recent measured SeoRebuild per product_name

    Rule: memory supports decisions. Returns at most one sentence.
    """
    now       = datetime.utcnow()
    past_cnt  = notif_counts.get(insight_key, 0)
    res_at    = resolved_history.get(insight_key)
    days_ago  = int((now - res_at).total_seconds() / 86400) if res_at else None

    # SEO: check whether a past rebuild actually worked for this product
    if rule_category == "seo_opportunity" and product_name:
        rb = rebuild_outcomes.get(product_name)
        if rb:
            if rb.winner and rb.delta_ctr_percent and rb.delta_ctr_percent > 0:
                return f"SEO-пересборка ранее восстанавливала CTR этого товара (+{rb.delta_ctr_percent:.1f}% CTR)"
            if rb.delta_ctr_percent is not None and rb.delta_ctr_percent <= 0:
                return "Пересборка ранее не дала результата — попробуйте другой стиль карточки"

    # Recurrence: pattern seen 2+ times in 90 days
    if past_cnt >= 2:
        if days_ago is not None:
            return f"Повторный паттерн — предыдущая стабилизация {days_ago} дн. назад"
        return f"Паттерн повторяется {past_cnt} раз за 90 дней"

    # Single prior stabilization within 60 days
    if days_ago is not None and days_ago <= 60:
        return f"Похожая ситуация была стабилизирована {days_ago} дн. назад"

    return None


def _adapt_recommendations(
    base_recs:        list[str],
    rule_category:    str,
    product_name:     str | None,
    insight_key:      str,
    resolved_history: dict[str, datetime],
    notif_counts:     dict[str, int],
    rebuild_outcomes: dict[str, "SeoRebuild"],
) -> list[str]:
    """
    Adapt recommendations based on historical outcomes.

    Proven interventions → promoted to top.
    Failed interventions → removed and replaced.
    Recurrent patterns   → systemic fix surfaces instead of generic advice.

    Returns at most 4 recommendations.
    """
    past_cnt    = notif_counts.get(insight_key, 0)
    recs        = list(base_recs)

    if rule_category == "seo_opportunity" and product_name:
        rb = rebuild_outcomes.get(product_name)
        if rb:
            if rb.winner and rb.delta_ctr_percent and rb.delta_ctr_percent > 0:
                proven = f"Авто-пересборка: ранее дала +{rb.delta_ctr_percent:.1f}% CTR для этого товара"
                recs   = [proven] + [r for r in recs if "пересборк" not in r.lower()]
            elif rb.delta_ctr_percent is not None and rb.delta_ctr_percent <= 0:
                recs = [r for r in recs if "пересборк" not in r.lower() and "авто" not in r.lower()]
                recs.insert(0, "Проверьте ценовое позиционирование — пересборка ранее не улучшила CTR")

    elif rule_category == "high_ad_spend" and past_cnt >= 2:
        recs = [r for r in recs if "оптимизировать ставки" not in r.lower()]
        recs.insert(0, f"Провести аудит ключевых слов по ROAS — паттерн повторяется {past_cnt}× за 90 дней")

    elif rule_category == "margin_crisis" and past_cnt >= 2:
        recs = [r for r in recs if "повышение цены" not in r.lower()]
        recs.insert(0, "Провести постатейный разбор затрат — ситуация системная, не разовая")

    elif rule_category == "low_stock" and past_cnt >= 2:
        recs.append("Рассмотрите систематический порог пополнения — ситуация повторяется")

    return recs[:4]


def _mp_memory(rule_category: str, marketplace: str) -> dict:
    """Resolve marketplace mechanic and return enrichment fields."""
    m = get_mechanic(rule_category, marketplace)
    return {
        "automation_level":      m["automation_level"],
        "marketplace_mechanic":  m["mechanic_name"],
        "marketplace_risk_note": m["risk_note"],
    }


def _normalize_cat(key: str) -> str:
    """Return base category slug; strips 'demo_' prefix for demo keys."""
    cat = _extract_category(key)
    return cat[len("demo_"):] if cat.startswith("demo_") else cat




























# Signal weights for preference scoring
async def _get_style_rec(
    user_id: str, category: str, marketplace: str, db: AsyncSession
) -> "StyleRecommendation | None":
    """Fetch style recommendation from seo_intelligence. Fails silently."""
    try:
        from routers.seo_intelligence import get_style_recommendation
        data = await get_style_recommendation(user_id, category, marketplace, db)
        if not data:
            return None
        return StyleRecommendation(**data)
    except Exception:
        return None


# ── Decision confidence enrichment ────────────────────────────────────────────



# ── Signal lifecycle enrichment ───────────────────────────────────────────────



# ── Operational summary helper ────────────────────────────────────────────────



# ── Execution sequencing enrichment (Sprint 32) ───────────────────────────────







































# ── Outcome feedback enrichment ───────────────────────────────────────────────



# ── Signal age decay enrichment ───────────────────────────────────────────────







# ── Demo insights ──────────────────────────────────────────────────────────────
#
# Causal story:
#   Блендер PowerBlend (WB): high ad spend [demo-2] → margin collapse [demo-3]
#   Магнитные биты (WB):     card quality blocks CTR despite good product [demo-1]
#   Ручной миксер (Ozon):    confirmed multi-period growth, Ozon attribution delay noted [demo-4]
#
# Restraint: 3 warnings + 1 positive. No redundant alerts.

# DEPRECATED (Step 3 — Demo/Real Separation): produces fabricated is_demo=True
# insights. NO LONGER called from the /insights data-path (get_insights returns a
# real empty/no_data response instead). Retained only for an explicit sample mode
# / rollback. Do NOT re-wire into get_insights.
async def _compute_insights(
    uid: str,
    db: AsyncSession,
    statuses: dict[str, tuple[str, str]],
    resolved_history: dict[str, datetime] | None = None,
    notif_counts:     dict[str, int]      | None = None,
    rebuild_outcomes: dict[str, SeoRebuild] | None = None,
) -> list[InsightItem]:

    _rh  = resolved_history or {}
    _nc  = notif_counts     or {}
    _rbo = rebuild_outcomes or {}

    def _mem(key: str, cat: str, pname: str | None) -> str | None:
        return _build_memory_note(key, cat, pname, _rh, _nc, _rbo)

    def _recs(base: list[str], cat: str, pname: str | None, key: str) -> list[str]:
        return _adapt_recommendations(base, cat, pname, key, _rh, _nc, _rbo)

    f_res = await db.execute(select(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))
    f_rows = f_res.scalars().all()

    p_res = await db.execute(select(ImportedProductRow).where(ImportedProductRow.user_id == uid))
    p_rows = p_res.scalars().all()

    if not f_rows and not p_rows:
        return []

    # Aggregate finance by (marketplace, sku)
    finance: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "revenue": 0.0, "ad_spend": 0.0, "net_profit": 0.0,
        "quantity": 0, "commission": 0.0, "logistics": 0.0,
        "title": None, "marketplace": None,
        "daily": defaultdict(float),
    })

    for row in f_rows:
        key = (row.marketplace, row.sku or "unknown")
        d = finance[key]
        d["revenue"]    += row.revenue
        d["ad_spend"]   += row.ad_spend
        d["net_profit"] += row.net_profit
        d["quantity"]   += row.quantity
        d["commission"] += row.commission
        d["logistics"]  += row.logistics
        d["marketplace"] = row.marketplace
        if row.title: d["title"] = row.title
        if row.date:  d["daily"][row.date] += row.revenue

    # Product data by (marketplace, sku)
    products: dict[tuple[str, str], ImportedProductRow] = {}
    for row in p_rows:
        products[(row.marketplace, row.sku)] = row

    # If only product rows (no finance), generate basic insights
    if not f_rows and p_rows:
        insights: list[InsightItem] = []
        idx = 0
        for row in p_rows:
            if row.stock is not None and 0 <= row.stock <= 5:
                key = build_insight_key("low_stock", row.marketplace, row.sku).key
                st = statuses.get(key, ("active", None))
                insights.append(InsightItem(
                    id=f"ins-{idx}", key=key,
                    type="warning", icon="⚠️",
                    title="Критически низкий остаток",
                    subtitle=f"{row.title or row.sku} · {_mp_label(row.marketplace)}",
                    reasons=[f"Остаток: {row.stock} шт"],
                    confidence=95, confidence_level="high",
                    impact=InsightImpact(label="Риск", estimate="Потеря позиций при out-of-stock", sign="negative"),
                    benchmark=None,
                    recommendations=_recs([
                        "Срочно пополнить склад",
                        "Временно снизить рекламные ставки",
                    ], "low_stock", row.title or row.sku, key),
                    actions=[InsightAction(label="Поставщики", url="/suppliers", type="primary")],
                    status=st[0], record_id=st[1],
                    product_name=row.title or row.sku, product_sku=row.sku,
                    marketplace=row.marketplace, is_demo=False,
                    **_mp_memory("low_stock", row.marketplace),
                    memory_context=_mem(key, "low_stock", row.title or row.sku),
                ))
                idx += 1
        return insights

    insights = []
    idx = 0

    for (mp, sku), fin in finance.items():
        rev    = fin["revenue"]
        ads    = fin["ad_spend"]
        profit = fin["net_profit"]
        qty    = fin["quantity"]
        title  = fin["title"] or sku
        bm     = _bm(mp)
        mplbl  = _mp_label(mp)

        prod   = products.get((mp, sku))
        stock  = prod.stock  if prod else None
        rating = prod.rating if prod else None

        if rev < 1000 and ads < 200:
            continue

        margin_pct = profit / rev if rev > 0 else None
        ad_ratio   = ads / rev   if rev > 0 else None
        rev_per_ad = rev / ads   if ads > 0 else None

        # ── Rule 1: SEO CTR Opportunity ────────────────────────────────────────
        if (
            rating is not None and rating >= 4.2
            and (stock is None or stock > 0)
            and ads >= 300
            and rev_per_ad is not None
            and rev_per_ad < bm["revenue_per_ad"] * 0.72
        ):
            key = build_insight_key("seo_opportunity", mp, sku).key
            st  = statuses.get(key, ("active", None))
            gap_pct  = round((bm["revenue_per_ad"] - rev_per_ad) / bm["revenue_per_ad"] * 100)
            conf     = min(88, 60 + gap_pct // 2)
            # monthly loss: efficiency gap * monthly ad spend
            monthly_ads = ads / max(len(fin["daily"]), 1) * 30
            loss_est = round(max(0, (bm["revenue_per_ad"] - rev_per_ad) * monthly_ads * 0.35), -2)

            insights.append(InsightItem(
                id=f"ins-{idx}", key=key,
                type="warning", icon="⚠️",
                title="Карточка товара вероятно снижает CTR",
                subtitle=f"{title} · {mplbl}",
                reasons=[
                    f"Эффективность рекламы ниже среднего по категории на {gap_pct}%",
                    f"Рейтинг товара хороший ({rating:.1f} ★)",
                    "Цена находится в рыночном диапазоне",
                    "Реклама активна — бюджет расходуется",
                ],
                recommendations=_recs([
                    "Увеличить product focus на главном слайде",
                    "Уменьшить объём текста на карточке",
                    "Усилить главный title",
                    "Сделать ярче первый слайд",
                ], "seo_opportunity", title, key),
                confidence=conf, confidence_level=_clevel(conf),
                impact=InsightImpact(
                    label="Примерный эффект",
                    estimate=f"{_fmt_k(loss_est)} потенциальной выручки",
                    sign="negative",
                ),
                benchmark=InsightBenchmark(
                    metric="Выручка на ₽ рекламы",
                    value=f"{rev_per_ad:.1f} ₽/₽",
                    baseline=f"median {bm['revenue_per_ad']:.1f} ₽/₽ по категории",
                    deviation=f"-{gap_pct}% ниже нормы",
                ),
                actions=[
                    InsightAction(
                        label="Товары и данные", type="primary",
                        url="/dashboard/data",
                        params={"product": title, "category": "auto", "auto": "1"},
                    ),
                    InsightAction(label="Товары и данные", type="secondary", url="/dashboard/data"),
                ],
                status=st[0], record_id=st[1],
                product_name=title, product_sku=sku,
                marketplace=mp, is_demo=False,
                impact_score=_impact_score(conf, loss_est),
                estimated_monthly_loss_rub=float(loss_est),
                style_recommendation=await _get_style_rec(uid, "auto", mp, db),
                **_mp_memory("seo_opportunity", mp),
                memory_context=_mem(key, "seo_opportunity", title),
                sim_meta={
                    "days_active": max(len(fin["daily"]), 1),
                    "product_name": title,
                },
            ))
            idx += 1

        # ── Rule 2: High Ad Spend (maturity-aware, growth vs degradation) ────────
        if (
            ad_ratio is not None
            and ad_ratio > bm["ad_spend_ratio_median"] * 2.0
            and ads >= 1000
        ):
            days_active = max(len(fin["daily"]), 1)
            alert_ok, ctx_note, conf_penalty = _ad_degradation_context(fin["daily"], days_active)

            if alert_ok:
                key          = build_insight_key("high_ad_spend", mp, sku).key
                st           = statuses.get(key, ("active", None))
                excess_ratio = ad_ratio - bm["ad_spend_ratio_median"]
                dev_pct      = round(excess_ratio / bm["ad_spend_ratio_median"] * 100)
                monthly_rev  = rev / days_active * 30
                excess_rub   = round(excess_ratio * monthly_rev, -2)
                conf         = min(92, max(55, 70 + dev_pct // 10 - conf_penalty))

                eff_note = (
                    "Текущая эффективность рекламы не компенсирует рост затрат"
                    if margin_pct is not None and margin_pct < bm["margin_median"] * 0.7
                    else f"Маржинальность: {margin_pct*100:.0f}%" if margin_pct is not None
                    else "Маржинальность снижена"
                )

                insights.append(InsightItem(
                    id=f"ins-{idx}", key=key,
                    type="warning", icon="⚠️",
                    title="Нагрузка рекламных расходов превышает устойчивый диапазон",
                    subtitle=f"{title} · {mplbl}",
                    reasons=[
                        f"ДРР: {ad_ratio*100:.0f}% — выше диапазона {bm['ad_spend_ratio_median']*100:.0f}–{bm['ad_spend_ratio_median']*100*1.5:.0f}% в течение {days_active} дн.",
                        eff_note,
                        ctx_note,
                    ],
                    recommendations=_recs([
                        "Проверить конверсию ключевых слов — часть трафика может быть нецелевой",
                        "Скорректировать ставки на основе ROAS по каждому ключу",
                        "Рассмотреть перераспределение бюджета на органический SEO-рост",
                    ], "high_ad_spend", title, key),
                    confidence=conf, confidence_level=_clevel(conf),
                    impact=InsightImpact(
                        label="Избыточная нагрузка",
                        estimate=f"{_fmt_k(max(0, excess_rub))} сверх устойчивого диапазона",
                        sign="negative",
                    ),
                    benchmark=InsightBenchmark(
                        metric="Доля рекламных расходов (ДРР)",
                        value=f"{ad_ratio*100:.0f}%",
                        baseline=f"median {bm['ad_spend_ratio_median']*100:.0f}% по категории",
                        deviation=f"+{dev_pct}% выше нормы",
                    ),
                    actions=[
                        InsightAction(label="Открыть Пульт", url="/dashboard", type="primary"),
                        InsightAction(label="Экономика", url="/profit-calculator", type="secondary"),
                    ],
                    status=st[0], record_id=st[1],
                    product_name=title, product_sku=sku,
                    marketplace=mp, is_demo=False,
                    impact_score=_impact_score(conf, max(0, excess_rub)),
                    estimated_monthly_loss_rub=float(max(0, excess_rub)),
                    **_mp_memory("high_ad_spend", mp),
                    memory_context=_mem(key, "high_ad_spend", title),
                    sim_meta={
                        "days_active": days_active,
                        "ad_ratio_pct": (ad_ratio or 0) * 100,
                        "margin_pct": (margin_pct or 0) * 100,
                        "product_name": title,
                    },
                ))
                idx += 1

        # ── Rule 3: Margin Crisis (pressure-aware, category-contextual) ──────────
        if margin_pct is not None and margin_pct < 0.05 and rev >= 5000:
            days_active = max(len(fin["daily"]), 1)
            alert_ok, src, ctx_reasons, src_recs, conf_penalty = _margin_pressure_context(
                daily_rev=fin["daily"],
                margin_pct=margin_pct,
                ad_ratio=ad_ratio,
                commission=fin["commission"],
                logistics=fin["logistics"],
                rev=rev,
                bm=bm,
            )

            if alert_ok:
                key         = build_insight_key("margin_crisis", mp, sku).key
                st          = statuses.get(key, ("active", None))
                gap_pp      = round((bm["margin_median"] - margin_pct) * 100)
                monthly_rev = rev / days_active * 30
                potential   = round(monthly_rev * (bm["margin_median"] - margin_pct), -2)
                base_conf   = 85 if margin_pct < 0 else 72
                conf        = max(52, base_conf - conf_penalty)

                margin_reasons = [
                    f"Маржа: {margin_pct*100:.1f}% — разрыв {gap_pp} п.п. до медианы категории ({bm['margin_median']*100:.0f}%)",
                ] + ctx_reasons

                insights.append(InsightItem(
                    id=f"ins-{idx}", key=key,
                    type="warning", icon="⚠️",
                    title=_MARGIN_TITLES.get(src, _MARGIN_TITLES["structural"]),
                    subtitle=f"{title} · {mplbl}",
                    reasons=margin_reasons,
                    recommendations=_recs(src_recs, "margin_crisis", title, key),
                    confidence=conf, confidence_level=_clevel(conf),
                    impact=InsightImpact(
                        label="Потенциал при выходе на median",
                        estimate=f"+{_fmt_k(max(0, potential))} в месяц",
                        sign="positive",
                    ),
                    benchmark=InsightBenchmark(
                        metric="Маржинальность",
                        value=f"{margin_pct*100:.1f}%",
                        baseline=f"median {bm['margin_median']*100:.0f}% по категории",
                        deviation=f"-{gap_pp} п.п. ниже нормы",
                    ),
                    actions=[
                        InsightAction(label="Экономика", url="/profit-calculator", type="primary"),
                        InsightAction(label="Мои данные", url="/dashboard/data", type="secondary"),
                    ],
                    status=st[0], record_id=st[1],
                    product_name=title, product_sku=sku,
                    marketplace=mp, is_demo=False,
                    impact_score=_impact_score(conf, max(0, potential)),
                    estimated_monthly_gain_rub=float(max(0, potential)),
                    **_mp_memory("margin_crisis", mp),
                    memory_context=_mem(key, "margin_crisis", title),
                    sim_meta={
                        "days_active": days_active,
                        "margin_pct": (margin_pct or 0) * 100,
                        "pressure_source": src,
                        "product_name": title,
                    },
                ))
                idx += 1

        # ── Rule 4: Sales Growth (maturity-confirmed, not spike) ──────────────
        daily = fin["daily"]
        is_mature, periods, growth_pct, cv = _growth_maturity(daily)
        if is_mature:
            key    = build_insight_key("sales_growth", mp, sku).key
            st     = statuses.get(key, ("active", None))
            # Use most recent 3 days sum for uplift estimate
            dates_s  = sorted(daily.keys())
            last3sum = sum(daily[d] for d in dates_s[-3:])
            prev3sum = sum(daily[d] for d in dates_s[-6:-3]) if len(dates_s) >= 6 else last3sum
            uplift   = round(max(0, (last3sum - prev3sum) * 10), -2)
            # Confidence: periods confirmed + growth magnitude
            conf = min(88, 58 + periods * 8 + growth_pct // 4)
            period_label = f"{periods} периода подряд" if periods == 2 else "3 периода подряд"
            cv_note = f"Дисперсия: {cv:.2f} — рост равномерный, не разовый всплеск"

            insights.append(InsightItem(
                id=f"ins-{idx}", key=key,
                type="positive", icon="📈",
                title=f"Рост подтверждён в {period_label} (+{growth_pct}%)",
                subtitle=f"{title} · {mplbl}",
                reasons=[
                    f"Рост выручки +{growth_pct}% подтверждён в {period_label}",
                    f"Сигнал прошёл проверку на всплеск: {cv_note}",
                    "Разовые пики исключены — паттерн устойчивый",
                ],
                recommendations=[
                    "Масштабировать рекламный бюджет на этот товар",
                    "Убедиться, что склад не опустеет при сохранении темпа",
                    "Применить схему карточки к аналогичным товарам",
                ],
                confidence=conf, confidence_level=_clevel(conf),
                impact=InsightImpact(
                    label="Оценка устойчивого роста",
                    estimate=f"+{_fmt_k(uplift)} при сохранении динамики",
                    sign="positive",
                ),
                benchmark=None,
                actions=[
                    InsightAction(label="Экономика", url="/profit-calculator", type="primary"),
                ],
                status=st[0], record_id=st[1],
                product_name=title, product_sku=sku,
                marketplace=mp, is_demo=False,
                impact_score=_impact_score(conf, uplift),
                estimated_monthly_gain_rub=float(uplift),
                **_mp_memory("sales_growth", mp),
                memory_context=_mem(key, "sales_growth", title),
                sim_meta={
                    "days_active": len(daily),
                    "growth_pct": growth_pct,
                    "product_name": title,
                },
            ))
            idx += 1

        # ── Rule 5: Low Stock ──────────────────────────────────────────────────
        if stock is not None and 0 <= stock <= 5:
            key = build_insight_key("low_stock", mp, sku).key
            st  = statuses.get(key, ("active", None))
            daily_avg = qty / max(len(daily), 1) if qty > 0 else 1
            days_left = round(stock / daily_avg) if daily_avg > 0 and daily_avg < stock else stock

            insights.append(InsightItem(
                id=f"ins-{idx}", key=key,
                type="warning", icon="⚠️",
                title="Критически низкий остаток",
                subtitle=f"{title} · {mplbl}",
                reasons=[
                    f"Остаток: {stock} шт",
                    f"При текущих темпах — примерно {days_left} дн.",
                ],
                recommendations=_recs([
                    "Срочно пополнить склад",
                    "Временно снизить рекламные ставки",
                ], "low_stock", title, key),
                confidence=95, confidence_level="high",
                impact=InsightImpact(
                    label="Риск",
                    estimate="Потеря позиций в поиске при out-of-stock",
                    sign="negative",
                ),
                benchmark=None,
                actions=[
                    InsightAction(label="Поставщики", url="/suppliers", type="primary"),
                ],
                status=st[0], record_id=st[1],
                product_name=title, product_sku=sku,
                marketplace=mp, is_demo=False,
                impact_score=_impact_score(95, rev * 0.2),
                **_mp_memory("low_stock", mp),
                memory_context=_mem(key, "low_stock", title),
                sim_meta={
                    "days_active": max(len(fin["daily"]), 1),
                    "stock": stock,
                    "days_left": days_left,
                    "product_name": title,
                },
            ))
            idx += 1

        # ── Rule 6: High Rating (positive) ────────────────────────────────────
        if rating is not None and rating >= 4.8 and rev >= 3000:
            key = build_insight_key("high_rating", mp, sku).key
            st  = statuses.get(key, ("active", None))

            insights.append(InsightItem(
                id=f"ins-{idx}", key=key,
                type="positive", icon="⭐",
                title=f"Рейтинг товара достиг {rating:.1f} ★",
                subtitle=f"{title} · {mplbl}",
                reasons=[
                    f"Рейтинг {rating:.1f} — топ по категории",
                    "Высокий рейтинг улучшает позиции в органическом поиске",
                ],
                recommendations=[
                    "Используйте рейтинг в заголовке карточки",
                    "Масштабируйте рекламу — конверсия выше средней",
                ],
                confidence=90, confidence_level="high",
                impact=InsightImpact(
                    label="Возможность",
                    estimate="Высокий рейтинг даёт +15–25% к органическому трафику",
                    sign="positive",
                ),
                benchmark=InsightBenchmark(
                    metric="Рейтинг товара",
                    value=f"{rating:.1f} ★",
                    baseline=f"good ≥ {bm['rating_good']} ★ по категории",
                    deviation="Топ 10% по категории",
                ),
                actions=[
                    InsightAction(
                        label="Товары и данные", type="primary",
                        url="/dashboard/data",
                        params={"product": title, "auto": "1"},
                    ),
                ],
                status=st[0], record_id=st[1],
                product_name=title, product_sku=sku,
                marketplace=mp, is_demo=False,
                impact_score=_impact_score(90, rev * 0.15),
                estimated_monthly_gain_rub=float(round(rev * 0.15 / max(len(fin["daily"]), 1) * 30, -2)),
                **_mp_memory("high_rating", mp),
                memory_context=_mem(key, "high_rating", title),
            ))
            idx += 1

    def _sort_key(ins: InsightItem) -> tuple:
        s  = {"active": 0, "monitoring": 1, "resolved": 2, "dismissed": 3}
        t  = {"warning": 0, "positive": 1, "info": 2}
        cw = {"high": 1.0, "medium": 0.7, "low": 0.4}.get(ins.confidence_level, 0.5)
        priority_score = (ins.impact_score or 0) * cw
        return (s.get(ins.status, 3), t.get(ins.type, 2), -priority_score)

    insights.sort(key=_sort_key)

    # Sprint 20: enrich with marketplace behavior memory
    # Sprint 21: enrich with retrospective outcome memory
    for ins in insights:
        cat = _normalize_cat(ins.key)
        mp  = ins.marketplace or ""

        slugs, note, win = _mp_behavior(cat, mp)
        ins.marketplace_patterns             = slugs
        ins.marketplace_behavior_note        = note
        ins.marketplace_stabilization_window = win

        resolved_at = _rh.get(ins.key)
        nc          = _nc.get(ins.key, 0)
        ev          = _eval_outcome(ins.key, cat, resolved_at, nc)
        if ev:
            ins.outcome_state        = ev.outcome
            ins.outcome_memory_note  = _build_outcome_note(ev)
            ins.outcome_confidence   = ev.confidence
            if ev.outcome in ("failed", "temporary", "repeated"):
                ins.recommendations = _apply_outcome_recs(
                    ins.recommendations, cat, ev.outcome
                )

    return insights


# ── Routes ─────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# ME-6 — Insight Execute Layer. Turns an insight into a real marketplace action
# through the SHARED executor. Action Engine stays a DECISION layer: it builds
# the plan (insight_mapping) and delegates execution to Executor.execute().
# ══════════════════════════════════════════════════════════════════════════════
from services.marketplace import executor as _executor                 # noqa: E402
from services.marketplace import insight_mapping as _imap              # noqa: E402
from services.marketplace import operation_key as _opkey              # noqa: E402
from ._op_http import resolve_client_key as _resolve_client_key, raise_if_reconcile as _raise_if_reconcile  # noqa: E402
from fastapi import Header as _Header                                  # noqa: E402
from models.review_response import ReviewResponse as _ReviewResponse   # noqa: E402
from models.product import Product as _Product                         # noqa: E402


class ExecuteInsightRequest(BaseModel):
    dry_run: bool = False
    overrides: dict = Field(default_factory=dict)   # campaign_id / cpm / card / price ...


class ExecuteInsightResponse(BaseModel):
    success: bool
    status: str                       # success | dry_run_ok | rejected | failed | needs_input | partial
    action_type: Optional[str] = None
    execution_id: Optional[str] = None
    message: str = ""
    automation_eligible: bool = False
    needs_input: list[str] = Field(default_factory=list)
    descriptor: dict = Field(default_factory=dict)
    results: list[dict] = Field(default_factory=list)   # batch (rating_good)


def _imap_negative_max() -> int:
    from services.marketplace.guard import NEGATIVE_RATING_MAX
    return NEGATIVE_RATING_MAX


@router.post("/insights/{insight_key:path}/execute", response_model=ExecuteInsightResponse)
async def execute_insight(
    insight_key: str,
    body: ExecuteInsightRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = _Header(None, alias="Idempotency-Key"),
):
    uid = current_user.id
    plan = await _imap.resolve_plan(db, uid, insight_key, body.overrides)

    if not plan.ready:
        return ExecuteInsightResponse(
            success=False, status="needs_input", action_type=plan.action_type,
            automation_eligible=plan.automation_eligible, needs_input=plan.needs_input,
            descriptor=plan.descriptor,
            message="Нужны дополнительные данные для выполнения" if plan.needs_input
                    else "Инсайт не поддерживает выполнение",
        )

    # ── Insight → Decision promotion (bridge Slice 1: intent fixation only) ───
    # Best-effort, non-blocking: fixates the Decision; never applies/executes,
    # never opens measurement, never alters the execute response. Only on this
    # explicit operator path — NOT in _compute_insights / dashboard / Telegram.
    # Slice 2: capture the promoted decision_id for ExecutionLog provenance only.
    # Blocked promotion (or any failure) leaves decision_id None — execution
    # proceeds unchanged.
    decision_id = None
    try:
        _ptype, _pmp, _psku = _imap.parse_key(insight_key)
        _desc = plan.descriptor or {}
        _sev = {"sales_growth": "gain", "high_rating": "gain"}.get(_ptype, "warn")
        pres = await _promote_decision(db, user_id=uid, insight=_PromotionDTO(
            insight_key=insight_key, itype=_ptype, marketplace=_pmp, sku=_psku,
            problem=_desc.get("reason") or _ptype,
            cause=_desc.get("reason"),
            effect=_desc.get("expected_effect"),
            action=_desc.get("action"),
            pnl_impact=None, severity=_sev, is_demo=False,
        ))
        decision_id = pres.decision_id if pres else None
    except Exception:
        logger.exception("insight promotion failed for %s", insight_key)

    # ── batch: rating_good publishes every prepared positive review ───────────
    if plan.batch:
        reviews = (
            await db.execute(
                select(_ReviewResponse)
                .join(_Product, _ReviewResponse.product_id == _Product.id)
                .where(
                    _Product.user_id == uid,
                    _ReviewResponse.rating > _imap_negative_max(),
                    _ReviewResponse.external_review_id.isnot(None),
                    _ReviewResponse.status.in_(("pending", "generated", "draft", "approved")),
                )
            )
        ).scalars().all()
        results: list[dict] = []
        published = 0
        for r in reviews:
            if not (r.response_text or "").strip():
                continue
            res = await _executor.execute(
                db=db, user_id=uid, action_type="publish_review_response",
                payload={"feedback_id": r.external_review_id, "text": r.response_text,
                         "rating": r.rating},
                mode="manual_l3", insight_key=insight_key, decision_id=decision_id,
                idempotency_key=_opkey.review_key(r.id), dry_run=body.dry_run,
            )
            results.append({"review_id": r.id, "status": res.status,
                            "execution_id": res.log_id, "error": res.error})
            if res.ok and not body.dry_run:
                r.status = "published"
                r.published_at = datetime.utcnow()
                r.execution_log_id = res.log_id
                published += 1
        await db.commit()
        status = "dry_run_ok" if body.dry_run else ("success" if results else "partial")
        return ExecuteInsightResponse(
            success=True, status=status, action_type=plan.action_type,
            automation_eligible=plan.automation_eligible, descriptor=plan.descriptor,
            results=results,
            message=f"Опубликовано: {published}" if not body.dry_run else f"Готово к публикации: {len(results)}",
        )

    # ── single action ─────────────────────────────────────────────────────────
    # Manual direct execution → client operation UUID (header). The promoted decision_id is provenance
    # only, never the executor key (per the approved hybrid: action_engine single = client UUID).
    op_key = _resolve_client_key(idempotency_key, dry_run=body.dry_run)
    res = await _executor.execute(
        db=db, user_id=uid, action_type=plan.action_type, payload=plan.payload,
        mode="manual_l3", insight_key=insight_key, decision_id=decision_id,
        idempotency_key=op_key, dry_run=body.dry_run,
    )
    _raise_if_reconcile(res)

    # ── Insight → Decision → ExecutionLog → Measurement OPEN (bridge Slice 3) ──
    # Best-effort, non-blocking, open-only. Real success + listing-grain action
    # (set_price/update_card) only; token resolved server-side; baseline honesty
    # owned downstream (null baseline when the metric is unreadable, never faked).
    # Never closes, never attributes, never alters the execute response.
    if res.status == "success" and not body.dry_run and decision_id:
        try:
            opened = await _open_measurement(
                db, user_id=uid, decision_id=decision_id, action_key=plan.action_type,
                marketplace=res.marketplace, entity_id=plan.payload.get("offer_id"),
            )
            if opened is not None:
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("measurement open failed for decision %s", decision_id)

    return ExecuteInsightResponse(
        success=res.ok, status=res.status, action_type=plan.action_type,
        execution_id=res.log_id, automation_eligible=plan.automation_eligible,
        descriptor=plan.descriptor,
        message={"success": "executed", "dry_run_ok": "проверка пройдена",
                 "rejected": "отклонено guard/валидацией",
                 "failed": "ошибка маркетплейса"}.get(res.status, res.status),
    )
