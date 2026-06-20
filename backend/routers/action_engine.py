"""
Action Engine — /api/insights
Rule-based operational intelligence. No fake ML — pure heuristics on real data.
"""
from __future__ import annotations

import logging
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from dependencies import get_current_user
from sqlalchemy import func as sa_func

from models.user import User
from models.insight import InsightRecord
from models.imported_finance import ImportedFinanceRow
from models.user_event import UserEvent
from models.imported_product import ImportedProductRow
from models.telegram_notification_log import TelegramNotificationLog
from models.seo_rebuild import SeoRebuild
from data.marketplace_mechanics import get_mechanic, AutomationLevel
from services.insight_keys import build_insight_key
from services.insight_decision_bridge import (
    promote_insight_to_decision as _promote_decision,
    InsightPromotionDTO as _PromotionDTO,
)
from services.execution_measurement_bridge import (
    open_measurement_for_execution as _open_measurement,
)
from logic.simulation import generate_scenarios_for_insight as _gen_scenarios
from logic.focus_engine import compute_operational_focus, compress_scenarios as _compress_scenarios
from logic.focus_engine import OperationalFocus as _OperationalFocusDC
from logic.operator_profile import load_profile as _load_profile, apply_adaptations as _apply_adaptations
from logic.decision_weight import (
    apply_decision_weights as _apply_weights,
    compute_fatigue_score,
    compute_stability_credit,
)
from logic.marketplace_behavior import behavior_note_for_insight as _mp_behavior
from logic.outcome_memory import (
    evaluate_resolution_outcome as _eval_outcome,
    build_outcome_note as _build_outcome_note,
    apply_outcome_to_recommendations as _apply_outcome_recs,
)
from logic.portfolio_patterns import (
    detect_portfolio_patterns as _detect_portfolio,
    insight_to_summary as _insight_summary,
)
from logic.decision_confidence import compute_decision_confidence as _compute_conf
from logic.signal_lifecycle import compute_signal_lifecycle as _compute_lifecycle
from logic.operational_summary import build_operational_summary as _build_op_summary
from logic.outcome_feedback import evaluate_operator_action as _eval_feedback
from logic.signal_decay import compute_signal_decay as _compute_decay
from logic.execution_sequencing import (
    build_execution_sequence as _build_sequence,
    sequencing_summary_line  as _seq_summary_line,
    SequencedAction as _SequencedActionDC,
)
from logic.operational_trajectory import (
    compute_operational_trajectory as _compute_trajectory,
    trajectory_weight_delta        as _traj_weight_delta,
)
from logic.tradeoff_intelligence import (
    get_tradeoff     as _get_tradeoff,
    build_tradeoff_note as _build_tradeoff_note,
)
from logic.failure_forecast import compute_failure_forecast as _compute_forecast
from logic.recovery_paths import compute_recovery_path as _compute_recovery
from logic.operator_capacity import compute_operator_capacity as _compute_capacity
from logic.stabilization_lock import compute_stabilization_lock as _compute_lock
from logic.counterfactual_pressure import (
    compute_counterfactual_pressure as _compute_cf,
    COUNTERFACTUAL_WEIGHT_DELTA     as _CF_WEIGHT_DELTA,
)
from logic.operator_strategy_profile import compute_operator_strategy_profile as _compute_strategy
from logic.comparative_simulation import (
    compute_path_comparison as _compute_comparison,
    PathComparison as _PathComparisonDC,
)
from logic.strategy_commitment import compute_strategy_commitment as _compute_commitment
from logic.observability_recovery import compute_observability_recovery as _compute_obs_recovery, obs_window_label as _obs_window_label
from logic.opportunity_cost import compute_opportunity_cost as _compute_oc
from logic.decision_drift import compute_decision_drift as _compute_drift
from logic.intervention_timing import compute_intervention_timing as _compute_timing
from logic.intervention_reversal import compute_intervention_reversal as _compute_reversal
from logic.cascade_pressure import (
    compute_cascade_pressure as _compute_cascade,
    CASCADE_WEIGHT_DELTA     as _CASCADE_W_DELTA,
)
from logic.resilience_snapshot   import compute_resilience_snapshot   as _compute_resilience
from logic.resilience_trajectory import compute_resilience_trajectory as _compute_resilience_traj
from logic.adaptive_capacity      import compute_adaptive_capacity      as _compute_adaptive_cap
from logic.strategic_memory_drift import compute_strategic_memory_drift as _compute_mem_drift
from logic.operational_regime import compute_operational_regime as _compute_regime, REGIME_WEIGHT_MULTIPLIER as _REGIME_MULTIPLIER
from logic.decision_energy import compute_decision_energy as _compute_energy, ENERGY_WEIGHT_DELTA as _ENERGY_DELTA
from logic.phase_transition import compute_phase_transition as _compute_phase, PHASE_WEIGHT_MULTIPLIER as _PHASE_MULTIPLIER
from logic.stability_topology import compute_stability_topology as _compute_topology, TOPOLOGY_WEIGHT_MULTIPLIER as _TOPO_MULTIPLIER
from logic.operational_doctrine import compute_operational_doctrine as _compute_doctrine, DOCTRINE_WEIGHT_MULTIPLIER as _DOCTRINE_MULTIPLIER
from logic.institutional_inertia import compute_institutional_inertia as _compute_inertia, INERTIA_WEIGHT_MULTIPLIER as _INERTIA_MULTIPLIER
from logic.structural_recovery_capacity import compute_structural_recovery_capacity as _compute_recovery_cap, RECOVERY_CAPACITY_WEIGHT_MULTIPLIER as _RECOVERY_CAP_MULTIPLIER
from logic.recovery_path_topology import compute_recovery_path_topology as _compute_path_topo, PATH_BASE_MULTIPLIERS as _PATH_TOPO_MULTIPLIERS
from models.operator_decision import OperatorDecision

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


class InsightsResponse(BaseModel):
    insights:               list[InsightItem]
    focused_insights:       list[InsightItem]
    operational_chains:     list[OperationalChain] = []
    operational_scenarios:  list[OperationalScenario] = []
    operational_focus:      Optional[FocusBlock] = None
    portfolio_patterns:     list[PortfolioPattern] = []
    operational_summary:    Optional[OperationalSummaryOut] = None  # Sprint 25
    stabilization_sequence: list[SequencedActionOut] = []           # Sprint 32
    operational_capacity:       Optional[OperationalCapacityOut]       = None # Sprint 37
    operator_strategy_profile: Optional[OperatorStrategyProfileOut]   = None # Sprint 40
    strategy_commitment:       Optional[StrategyCommitmentOut]        = None # Sprint 43
    decision_drift:            Optional[DecisionDriftOut]             = None # Sprint 47
    operational_regime:        Optional[OperationalRegimeOut]         = None # Sprint 55
    decision_energy:           Optional[DecisionEnergyOut]            = None # Sprint 56
    operational_phase_transition: Optional[OperationalPhaseTransitionOut] = None # Sprint 57
    stability_topology:           Optional[StabilityTopologyOut]           = None # Sprint 58
    operational_doctrine:         Optional[OperationalDoctrineOut]         = None # Sprint 59
    institutional_inertia:        Optional[InstitutionalInertiaOut]        = None # Sprint 60
    structural_recovery_capacity: Optional[StructuralRecoveryCapacityOut]  = None # Sprint 61
    structural_recovery_path_topology: Optional[StructuralRecoveryPathTopologyOut] = None # Sprint 62
    fatigue_score:              Optional[float] = None   # 0-1; high = PULT more selective
    stability_credit:       Optional[float] = None   # 0-1; high = stable operator
    is_demo:                bool
    total_active:           int
    has_data:               bool
    total_warnings:         int
    total_positive:         int
    estimated_monthly_loss: float


class UpdateStatusRequest(BaseModel):
    status: Literal["monitoring", "resolved", "dismissed", "active"]


class UpdateStatusResponse(BaseModel):
    ok:         bool
    record_id:  str
    new_status: str


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def detect_operational_chains(
    insights: list[InsightItem],
) -> tuple[list[OperationalChain], dict[str, str]]:
    """
    Detect causal relationships between co-occurring insights.
    Returns (chains[:3], secondary_map {insight_id: chain_id}).

    Rules checked in priority order; first match wins per consequence insight:
      1. high_ad_spend + margin_crisis (same product+mp)   → degradation
      2. seo_opportunity + high_ad_spend (same product+mp) → degradation
    """
    active = [i for i in insights if i.status not in ("resolved", "dismissed")]

    by_product: dict[tuple[str | None, str | None], dict[str, InsightItem]] = defaultdict(dict)
    for ins in active:
        ncat = _normalize_cat(ins.key)
        by_product[(ins.product_name, ins.marketplace)][ncat] = ins

    chains: list[OperationalChain] = []
    secondary_map: dict[str, str] = {}  # insight_id → chain_id

    for (pname, mp), cat_map in by_product.items():
        if not pname:
            continue

        # Rule 1: high_ad_spend → margin_crisis
        root = cat_map.get("high_ad_spend")
        consequence = cat_map.get("margin_crisis")
        if root and consequence and consequence.id not in secondary_map:
            conf = min(root.confidence, consequence.confidence)
            cid  = f"chain-adspend-margin-{root.id}"
            chains.append(OperationalChain(
                id=cid, type="degradation",
                root_insight_key=root.key,
                consequence_insight_key=consequence.key,
                root_title="Рекламная нагрузка",
                consequence_title="Снижение маржинальности",
                chain_text=(
                    f"{pname}: рекламные расходы — первопричина падения маржи. "
                    f"Оба сигнала решаются единым действием."
                ),
                evidence=[
                    "ДРР превышает норму при одновременном снижении маржи",
                    "Оба сигнала относятся к одному товару и площадке",
                    "Оптимизация рекламного бюджета устранит оба сигнала",
                ],
                confidence=conf, confidence_level=_clevel(conf),
                product_name=pname, marketplace=mp,
            ))
            secondary_map[consequence.id] = cid

        # Rule 2: seo_opportunity → high_ad_spend
        root2 = cat_map.get("seo_opportunity")
        consequence2 = cat_map.get("high_ad_spend")
        if root2 and consequence2 and consequence2.id not in secondary_map:
            conf2 = min(root2.confidence, consequence2.confidence, 72)
            cid2  = f"chain-seo-adspend-{root2.id}"
            chains.append(OperationalChain(
                id=cid2, type="degradation",
                root_insight_key=root2.key,
                consequence_insight_key=consequence2.key,
                root_title="Слабая карточка товара",
                consequence_title="Рекламная нагрузка",
                chain_text=(
                    f"{pname}: низкий CTR карточки вынуждает компенсировать "
                    f"органические потери платным трафиком."
                ),
                evidence=[
                    "CTR ниже нормы при хорошем рейтинге — потери на уровне карточки",
                    "Рекламный бюджет компенсирует органику, не создавая устойчивый рост",
                    "Пересборка карточки снизит зависимость от платного трафика",
                ],
                confidence=conf2, confidence_level=_clevel(conf2),
                product_name=pname, marketplace=mp,
            ))
            secondary_map[consequence2.id] = cid2

    chains.sort(key=lambda c: c.confidence, reverse=True)
    top = chains[:3]
    top_ids = {c.id for c in top}
    return top, {iid: cid for iid, cid in secondary_map.items() if cid in top_ids}


def _collect_scenarios(
    insights:         list[InsightItem],
    secondary_map:    dict[str, str],
    rebuild_outcomes: dict[str, "SeoRebuild"] | None = None,
    notif_counts:     dict[str, int] | None = None,
) -> list[OperationalScenario]:
    """
    Generate OperationalScenarios for all active non-secondary insights
    that carry sim_meta. Returns list sorted by confidence desc.
    """
    secondary_keys = {ins.key for ins in insights if ins.id in secondary_map}
    out: list[OperationalScenario] = []

    for ins in insights:
        if (
            ins.status in ("resolved", "dismissed")
            or ins.is_secondary
            or ins.key in secondary_keys
            or not ins.sim_meta
            or not ins.marketplace
        ):
            continue
        cat  = _normalize_cat(ins.key)
        raw  = _gen_scenarios(
            insight_key=ins.key,
            rule_category=cat,
            marketplace=ins.marketplace,
            insight_confidence=ins.confidence,
            meta=ins.sim_meta,
            rebuild_outcomes=rebuild_outcomes,
            notif_counts=notif_counts,
        )
        out.extend(OperationalScenario(**s) for s in raw)

    out.sort(key=lambda s: s.confidence, reverse=True)
    return _compress_scenarios(out)


import dataclasses as _dc


def _apply_commitment_weights(insights: list, commitment_state: str) -> None:
    delta = {
        "fragmented":  +8,
        "abandoned":   +12,
        "stabilizing": -10,
        "active":      -6,
    }.get(commitment_state, 0)
    if delta == 0:
        return
    for ins in insights:
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w + delta))


def _apply_regime_weights(insights: list, regime: str) -> None:
    """Apply regime weight multiplier to recurring/confirmed non-stale non-isolated insights."""
    multiplier = _REGIME_MULTIPLIER.get(regime, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        if getattr(ins, "cascade_state", None) == "isolated" and getattr(ins, "is_secondary", False):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_energy_weights(insights: list, energy_state: str) -> None:
    """Apply energy weight delta to recurring/confirmed active insights before focus ranking."""
    delta = _ENERGY_DELTA.get(energy_state, 0)
    if delta == 0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w + delta))


def _apply_phase_weights(insights: list, phase: str) -> None:
    """Apply phase weight multiplier to recurring/confirmed non-stale insights before focus ranking."""
    multiplier = _PHASE_MULTIPLIER.get(phase, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_topology_weights(insights: list, topology_state: str) -> None:
    """Apply topology weight multiplier to recurring/confirmed non-stale insights before focus ranking."""
    multiplier = _TOPO_MULTIPLIER.get(topology_state, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_doctrine_weights(insights: list, doctrine_state: str) -> None:
    """Apply doctrine weight multiplier to recurring/confirmed non-stale insights before focus ranking."""
    multiplier = _DOCTRINE_MULTIPLIER.get(doctrine_state, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_inertia_weights(insights: list, inertia_state: str) -> None:
    """Apply inertia weight multiplier to recurring/confirmed non-stale insights before focus ranking."""
    multiplier = _INERTIA_MULTIPLIER.get(inertia_state, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_recovery_cap_weights(insights: list, recovery_state: str) -> None:
    """Apply recovery capacity multiplier to recurring/confirmed non-stale insights before focus ranking."""
    multiplier = _RECOVERY_CAP_MULTIPLIER.get(recovery_state, 1.0)
    if multiplier == 1.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * multiplier))


def _apply_path_topology_weights(insights: list, path_topology_dc) -> None:
    """Apply density-modulated path topology weight to recurring/confirmed non-stale insights.
    effective_weight = PATH_BASE_MULTIPLIERS[dominant_path_type] × (1 − recovery_path_density)
    """
    base_mult = _PATH_TOPO_MULTIPLIERS.get(path_topology_dc.dominant_path_type, 1.00)
    density   = path_topology_dc.recovery_path_density
    effective  = base_mult * (1.0 - density)
    if effective == 0.0:
        return
    for ins in insights:
        if getattr(ins, "signal_lifecycle_stage", None) not in ("recurring", "confirmed", "persistent"):
            continue
        if getattr(ins, "signal_decay_state", None) in ("stale", "fading"):
            continue
        w = getattr(ins, "weight", None)
        if w is not None:
            ins.weight = max(0.0, min(100.0, w * effective))


def _build_focus(
    insights:         list[InsightItem],
    chains:           list[OperationalChain],
    scenarios:        list[OperationalScenario],
    resolved_history: dict[str, datetime] | None = None,
    notif_counts:     dict[str, int] | None = None,
    stability_credit: float = 0.0,
) -> Optional[FocusBlock]:
    focus_dc = compute_operational_focus(
        insights=insights,
        chains=chains,
        scenarios=scenarios,
        resolved_history=resolved_history,
        notif_counts=notif_counts,
        stability_credit=stability_credit,
    )
    if focus_dc is None:
        return None
    return FocusBlock(**_dc.asdict(focus_dc))


# Signal weights for preference scoring
_PREF_WEIGHTS: dict[str, float] = {
    "insight_resolved":    3.0,   # user fixed it → strongly relevant category
    "copilot_cta_clicked": 2.0,   # user acted on hint → relevant
    "insight_opened":      0.5,   # user expanded → mild interest
    "copilot_dismissed":  -1.0,   # user dismissed → less relevant
    "insight_snoozed":    -1.5,   # user snoozed → less relevant
}

_PREF_DECAY_LAMBDA = 0.05   # half-life ≈ 14 days
_PREF_CLAMP_LO     = -20.0
_PREF_CLAMP_HI     = +15.0
_CRITICAL_FLOOR    = 75.0   # critical insights: effective rank never falls below this


class _PrefData:
    __slots__ = ("modifier", "memory_decay")
    def __init__(self, modifier: float, memory_decay: float) -> None:
        self.modifier     = modifier      # net score delta, clamped to [-20, +15]
        self.memory_decay = memory_decay  # recency factor [0–1]: 0 = no signals, ~1 = all recent


async def _compute_preference_scores(
    uid: str,
    categories: set[str],
    db: AsyncSession,
    window_days: int = 30,
) -> dict[str, "_PrefData"]:
    """
    Returns modifier + memory_decay per insight category.

    modifier:     behavioral score delta (positive → acted on, negative → dismissed)
    memory_decay: how recent the signals are [0–1]; decays to 0 after ~60 days of inactivity

    Critical insights are protected by a score floor in _focused_filter.
    """
    if not categories:
        return {}

    since = datetime.utcnow() - timedelta(days=window_days)
    try:
        rows = await db.execute(
            select(UserEvent.event_type, UserEvent.entity_id, UserEvent.created_at)
            .where(
                UserEvent.user_id    == uid,
                UserEvent.created_at >= since,
                UserEvent.event_type.in_(list(_PREF_WEIGHTS)),
            )
        )
    except Exception as exc:
        logger.warning("preference_scores_query_failed", extra={"user_id": uid, "error": str(exc)})
        return {}

    now = datetime.utcnow()
    raw_scores:         dict[str, float] = defaultdict(float)
    weighted_decay_sum: dict[str, float] = defaultdict(float)
    weight_abs_sum:     dict[str, float] = defaultdict(float)

    for row in rows:
        if not row.entity_id:
            continue
        cat = _extract_category(row.entity_id)
        if cat not in categories:
            continue
        days_ago  = max(0.0, (now - row.created_at).total_seconds() / 86_400)
        decay     = math.exp(-_PREF_DECAY_LAMBDA * days_ago)
        w         = _PREF_WEIGHTS.get(row.event_type, 0.0)
        raw_scores[cat]         += w * decay
        weighted_decay_sum[cat] += abs(w) * decay
        weight_abs_sum[cat]     += abs(w)

    result: dict[str, _PrefData] = {}
    for cat in categories:
        modifier = max(_PREF_CLAMP_LO, min(_PREF_CLAMP_HI, raw_scores.get(cat, 0.0)))
        # memory_decay: weighted average of decay factors across all signals
        decay_val = (
            weighted_decay_sum[cat] / weight_abs_sum[cat]
            if weight_abs_sum.get(cat, 0.0) > 0 else 0.0
        )
        result[cat] = _PrefData(modifier=modifier, memory_decay=round(decay_val, 3))
    return result


def _focused_filter(
    insights: list["InsightItem"],
    pref_data: dict[str, "_PrefData"] | None = None,
    max_warnings: int = 3,
    include_debug: bool = False,
) -> list["InsightItem"]:
    """
    Curated surface list with preference-adaptive ranking.

    Rules:
    - Active only (no resolved/dismissed)
    - Med/high confidence warnings only
    - One warning per category (best effective rank wins)
    - Effective rank = impact_score + preference_modifier
    - Critical (impact_score ≥ 75, high confidence): rank floor at 75 — impossible to push below
    - At most max_warnings warnings + 1 positive
    - include_debug: attaches InsightDebug to each item (dev mode only)
    """
    pd = pref_data or {}

    def _modifier(ins: "InsightItem") -> float:
        cat = _extract_category(ins.key)
        return pd[cat].modifier if cat in pd else 0.0

    def _rank(ins: "InsightItem") -> float:
        cat  = _extract_category(ins.key)
        base = (ins.impact_score or 0) + _modifier(ins)
        if (ins.impact_score or 0) >= _CRITICAL_FLOOR and ins.confidence_level == "high":
            return max(base, _CRITICAL_FLOOR)
        return base

    active    = [i for i in insights if i.status not in ("resolved", "dismissed")]
    warnings  = [i for i in active   if i.type == "warning" and i.confidence_level != "low" and not i.is_secondary]
    positives = [i for i in active   if i.type == "positive"]

    seen: dict[str, "InsightItem"] = {}
    for w in sorted(warnings, key=_rank, reverse=True):
        cat = _extract_category(w.key)
        if cat not in seen:
            seen[cat] = w
    deduped = sorted(seen.values(), key=_rank, reverse=True)
    positives.sort(key=_rank, reverse=True)
    selected = deduped[:max_warnings] + positives[:1]

    if include_debug:
        annotated = []
        for ins in selected:
            cat  = _extract_category(ins.key)
            pref = pd.get(cat)
            mod  = pref.modifier if pref else 0.0
            dec  = pref.memory_decay if pref else 0.0
            annotated.append(ins.model_copy(update={"debug": InsightDebug(
                preference_modifier     = round(mod, 3),
                memory_decay            = dec,
                resurfaced_contextually = mod > 0.0,
            )}))
        return annotated

    return selected


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

def _enrich_decision_confidence(
    insights: list["InsightItem"],
    pp_raw: list,  # list of PortfolioPattern dataclasses from portfolio_patterns.py
) -> None:
    """
    Mutates each InsightItem in-place: sets decision_confidence_* fields.
    pp_raw: raw dataclass instances (not Pydantic) from detect_portfolio_patterns().
    """
    # Build map: category → most severe portfolio complexity
    _severity = {"systemic": 3, "moderate": 2, "localized": 1}
    pp_map: dict[str, str] = {}
    for p in pp_raw:
        for itype in (p.insight_types or []):
            cur = pp_map.get(itype)
            if cur is None or _severity.get(p.stabilization_complexity, 0) > _severity.get(cur, 0):
                pp_map[itype] = p.stabilization_complexity

    for ins in insights:
        cat = _normalize_cat(ins.key)
        port_cx = pp_map.get(cat)
        dc = _compute_conf(
            confidence=ins.confidence,
            signal_state=getattr(ins, "signal_state", None),
            outcome_state=getattr(ins, "outcome_state", None),
            marketplace_patterns=ins.marketplace_patterns or [],
            marketplace_stabilization_window=ins.marketplace_stabilization_window,
            automation_level=ins.automation_level,
            portfolio_complexity=port_cx,
            adaptation_note=ins.adaptation_note,
        )
        ins.decision_confidence_score  = dc.score
        ins.decision_confidence_band   = dc.confidence_band
        ins.decision_confidence_reason = dc.explanation
        ins.decision_stability_note    = dc.stability_note


# ── Signal lifecycle enrichment ───────────────────────────────────────────────

def _enrich_signal_lifecycle(
    insights: list["InsightItem"],
    resolved_history: dict[str, datetime],
    notif_counts: dict[str, int],
    first_seen_map: dict[str, datetime] | None = None,
) -> None:
    """
    Mutates each InsightItem in-place: sets signal_lifecycle_* fields.
    Requires Sprint 23 decision_confidence_band already set.
    """
    _fs = first_seen_map or {}
    for ins in insights:
        cat = _normalize_cat(ins.key)
        lc = _compute_lifecycle(
            insight_key=ins.key,
            rule_category=cat,
            first_seen=_fs.get(ins.key),
            resolved_at=resolved_history.get(ins.key),
            notif_count=notif_counts.get(ins.key, 0),
            outcome_state=ins.outcome_state,
            confidence_band=ins.decision_confidence_band,
        )
        ins.signal_lifecycle_stage   = lc.stage
        ins.signal_lifecycle_note    = lc.lifecycle_note
        ins.signal_lifecycle_weight  = lc.lifecycle_weight
        ins.signal_operational_age   = lc.days_in_state
        ins.signal_recurrence_count  = lc.recurrence_count


# ── Operational summary helper ────────────────────────────────────────────────

def _make_op_summary(
    insights:           list["InsightItem"],
    pp_raw:             list,
    resolved_history:   dict[str, datetime],
    fatigue_score:      float = 0.0,
    stability_credit:   float = 0.0,
) -> Optional["OperationalSummaryOut"]:
    """Build OperationalSummaryOut from current insight state. Returns None on error."""
    try:
        s = _build_op_summary(
            insights=insights,
            portfolio_patterns=pp_raw,
            resolved_history=resolved_history,
            fatigue_score=fatigue_score,
            stability_credit=stability_credit,
        )
        out = OperationalSummaryOut(**_dc.asdict(s))
        # Sprint 26: derive feedback evidence line from enriched insights
        out.outcome_feedback_line = _feedback_summary_line(insights)
        # Sprint 27: derive decay freshness summary line
        out.decay_summary_line = _decay_summary_line(insights)
        # Sprint 30: derive temporal momentum narrative
        out.momentum_summary_line = _momentum_summary_line(insights)
        return out
    except Exception as exc:
        logger.warning("op_summary_build_failed", extra={"error": str(exc)})
        return None


# ── Execution sequencing enrichment (Sprint 32) ───────────────────────────────

def _enrich_trajectory(
    insights:           list["InsightItem"],
    portfolio_patterns: list,
    operator_profile:   Any = None,
) -> None:
    """
    Mutate each InsightItem in-place with trajectory fields.
    Applies weight delta for focus engine (Sprint 33, Part 11).
    Must run AFTER lifecycle, decay, and sequencing enrichments.
    NEVER modifies raw confidence.
    """
    # Detect upstream-unresolved relationships for sequence failure detection
    _UPSTREAM_MAP: dict[str, list[str]] = {
        "margin_crisis":    ["high_ad_spend"],
        "seo_opportunity":  ["low_stock"],
    }
    active_cats = {i.key.split(":")[0] for i in insights if i.status not in ("resolved", "dismissed")}

    for ins in insights:
        cat = ins.key.split(":")[0]
        upstreams = _UPSTREAM_MAP.get(cat, [])
        upstream_unresolved = (
            (ins.sequence_stage or 0) >= 2
            and any(u in active_cats for u in upstreams)
        )

        traj = _compute_trajectory(
            insight=ins,
            lifecycle=ins.signal_lifecycle_stage,
            decay=ins.signal_decay_state,
            sequencing=None,
            portfolio_patterns=portfolio_patterns,
            operator_profile=operator_profile,
            upstream_unresolved=upstream_unresolved,
        )

        ins.trajectory_state          = traj.trajectory_state
        ins.trajectory_direction      = traj.trajectory_direction
        ins.reversibility_state       = traj.reversibility_state
        ins.stabilization_window_days = traj.stabilization_window_days
        ins.pressure_accumulation     = traj.pressure_accumulation
        ins.trajectory_note           = traj.trajectory_note

        # Sprint 33, Part 11: trajectory-based weight adjustment for focus priority
        delta = _traj_weight_delta(traj.trajectory_state)
        if delta != 0.0 and ins.weight is not None:
            ins.weight = max(0.0, min(100.0, ins.weight + delta))


def _enrich_tradeoff(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with tradeoff fields.
    Must run AFTER trajectory enrichment (uses trajectory_state for contextual note).
    Categories without registered tradeoffs get no fields set.
    """
    for ins in insights:
        cat = ins.key.split(":")[0]
        tradeoff = _get_tradeoff(cat)
        if tradeoff is None:
            continue
        ins.tradeoff_note         = _build_tradeoff_note(tradeoff, ins.trajectory_state)
        ins.tradeoff_severity     = tradeoff.severity
        ins.tradeoff_duration_days = tradeoff.expected_duration_days
        ins.reversibility_profile  = tradeoff.reversibility
        ins.stabilization_benefit  = tradeoff.stabilization_benefit


def _enrich_failure_forecast(insights: list["InsightItem"], portfolio_patterns: list) -> None:
    """
    Mutate each InsightItem in-place with failure forecast fields.
    Must run AFTER trajectory and tradeoff enrichments.
    Stable / no-data insights get forecast_fragility_state='stable' with low probability.
    """
    for ins in insights:
        # Build a lightweight trajectory proxy from already-enriched fields
        class _TrajProxy:
            trajectory_state    = ins.trajectory_state
            reversibility_state = ins.reversibility_state
            pressure_accumulation = ins.pressure_accumulation

        fc = _compute_forecast(
            insight=ins,
            lifecycle=ins.signal_lifecycle_stage,
            trajectory=_TrajProxy(),
            decay=ins.signal_decay_state,
            portfolio_patterns=portfolio_patterns,
        )
        ins.forecast_escalation_probability  = fc.escalation_probability
        ins.forecast_fragility_state         = fc.fragility_state
        ins.forecast_next_stage              = fc.predicted_next_stage
        ins.forecast_first_failure_mode      = fc.first_failure_mode
        ins.forecast_note                    = fc.forecast_note
        ins.forecast_instability_window_days = fc.instability_window_days


def _enrich_recovery_paths(insights: list["InsightItem"], portfolio_patterns: list) -> None:
    """
    Mutate each InsightItem in-place with recovery path fields.
    Must run AFTER failure_forecast enrichment (uses forecast_fragility_state).
    """
    for ins in insights:
        class _TrajProxy:
            reversibility_state = ins.reversibility_state

        class _FcProxy:
            forecast_fragility_state = ins.forecast_fragility_state

        rp = _compute_recovery(
            insight=ins,
            lifecycle=ins.signal_lifecycle_stage,
            trajectory=_TrajProxy(),
            portfolio_patterns=portfolio_patterns,
            forecast=_FcProxy(),
        )
        ins.recovery_probability          = rp.recovery_probability
        ins.recovery_state                = rp.recovery_state
        ins.first_recovered_metric        = rp.first_recovered_metric
        ins.lagging_metric                = rp.lagging_metric
        ins.expected_recovery_window_days = rp.expected_recovery_window_days
        ins.recovery_note                 = rp.recovery_note
        ins.recovery_dependency           = rp.recovery_dependency


def _enrich_stabilization_lock(insights: list["InsightItem"], capacity_state: str) -> None:
    """
    Mutate each InsightItem in-place with stabilization lock fields.
    Must run AFTER capacity enrichment (uses capacity_state).
    concurrent_active_count = number of active non-resolved insights with sequence_stage set.
    """
    concurrent_count = sum(
        1 for i in insights
        if i.status not in ("resolved", "dismissed")
        and (getattr(i, "sequence_stage", None) or 0) >= 1
    )
    for ins in insights:
        lk = _compute_lock(
            insight=ins,
            lifecycle=ins.signal_lifecycle_stage,
            trajectory_state=ins.trajectory_state,
            decay=ins.signal_decay_state,
            recovery_state=ins.recovery_state,
            concurrent_active_count=concurrent_count,
            age_days=ins.signal_age_days or 0,
            capacity_state=capacity_state,
        )
        ins.recovery_signal_state               = lk.recovery_signal_state
        ins.lock_estimated_recovery_window_days = lk.estimated_recovery_window_days
        ins.lock_reentry_condition              = lk.reentry_condition
        ins.lock_next_safe_action               = lk.next_safe_action


def _enrich_observability_recovery(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with observability recovery forecast.
    Must run AFTER stabilization_lock and comparative simulation enrichments.
    Uses recovery_signal_state, lock_estimated_recovery_window_days, trajectory_state.
    """
    concurrent_active = sum(
        1 for i in insights
        if i.status not in ("resolved", "dismissed")
        and getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing")
    )
    for ins in insights:
        rec = _compute_obs_recovery(ins, concurrent_active=concurrent_active)
        ins.obs_recovery_state       = rec.obs_recovery_state if rec.obs_recovery_state != "clear" else None
        ins.obs_recovery_window_days = rec.obs_recovery_window_days
        ins.obs_recovery_condition   = rec.obs_recovery_condition
        ins.obs_blocking_factor      = rec.obs_blocking_factor
        ins.obs_recovery_note        = rec.obs_recovery_note


def _enrich_timing(insights: list["InsightItem"], commitment_state: Optional[str]) -> None:
    """
    Mutate each InsightItem in-place with intervention timing intelligence.
    Explains when to intervene, when to wait, and how timing quality changes.
    Must run AFTER observability_recovery, counterfactual, stabilization_lock, and trajectory enrichments.
    """
    for ins in insights:
        t = _compute_timing(
            insight=ins,
            commitment_state=commitment_state,
            trajectory_state=ins.trajectory_state,
            reversibility_state=ins.reversibility_state,
            recovery_signal_state=ins.recovery_signal_state,
            obs_recovery_state=ins.obs_recovery_state,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            stabilization_window_days=ins.stabilization_window_days,
            counterfactual_transition_window_days=ins.counterfactual_transition_window_days,
            sequence_stage=ins.sequence_stage,
            forecast_fragility_state=ins.forecast_fragility_state,
            lock_reentry_condition=ins.lock_reentry_condition,
        )
        ins.timing_state                = t.timing_state
        ins.intervention_readiness      = t.intervention_readiness
        ins.timing_note                 = t.timing_note
        ins.optimal_window_days         = t.optimal_window_days
        ins.premature_intervention_risk = t.premature_intervention_risk
        ins.premature_risk_note         = t.premature_risk_note
        ins.delayed_intervention_risk   = t.delayed_intervention_risk
        ins.delayed_risk_note           = t.delayed_risk_note
        ins.waiting_benefit             = t.waiting_benefit
        ins.readiness_condition         = t.readiness_condition


def _enrich_reversal(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with intervention reversal intelligence.
    Identifies diminishing returns, overcorrection, and rollback economics.
    Must run AFTER trajectory, recovery paths, tradeoff, and outcome feedback enrichments.
    """
    for ins in insights:
        rv = _compute_reversal(
            insight=ins,
            trajectory_state=ins.trajectory_state,
            trajectory_direction=ins.trajectory_direction,
            recovery_state=ins.recovery_state,
            recovery_probability=ins.recovery_probability,
            outcome_state=ins.outcome_state,
            pressure_accumulation=ins.pressure_accumulation,
            reversibility_state=ins.reversibility_state,
            tradeoff_severity=ins.tradeoff_severity,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            stabilization_window_days=ins.stabilization_window_days,
        )
        ins.reversal_state              = rv.reversal_state
        ins.reversal_probability        = rv.reversal_probability
        ins.reversal_window_days        = rv.reversal_window_days
        ins.reversal_trigger            = rv.reversal_trigger
        ins.reversal_note               = rv.reversal_note
        ins.rollback_safety             = rv.rollback_safety
        ins.rollback_effect_expectation = rv.rollback_effect_expectation
        ins.stabilization_dependency    = rv.stabilization_dependency


def _enrich_opportunity_cost(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with opportunity cost intelligence.
    Explains how the cost of future decisions changes as action is deferred.
    Must run AFTER counterfactual, trajectory, and failure forecast enrichment.
    """
    for ins in insights:
        oc = _compute_oc(
            insight=ins,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            reversibility_state=ins.reversibility_state,
            pressure_accumulation=ins.pressure_accumulation,
            trajectory_state=ins.trajectory_state,
            forecast_instability_window_days=ins.forecast_instability_window_days,
        )
        ins.future_intervention_cost = oc.future_intervention_cost
        ins.reversibility_shift_note  = oc.reversibility_shift_note
        ins.opportunity_cost_note     = oc.opportunity_cost_note
        ins.dependency_note           = oc.dependency_note


def _enrich_cascade(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with secondary pressure cascade intelligence.
    Detects when stabilization in one zone creates pressure in adjacent operational zones.
    Must run AFTER _enrich_reversal(). Applies weight delta for focus engine.
    """
    for ins in insights:
        cc = _compute_cascade(
            insight_category=ins.key,
            trajectory_state=ins.trajectory_state,
            trajectory_direction=ins.trajectory_direction,
            reversal_state=ins.reversal_state,
            reversal_probability=ins.reversal_probability,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            pressure_accumulation=ins.pressure_accumulation,
            tradeoff_severity=ins.tradeoff_severity,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            stabilization_window_days=ins.stabilization_window_days,
            timing_state=ins.timing_state,
        )
        ins.cascade_state             = cc.cascade_state
        ins.cascade_direction         = cc.cascade_direction
        ins.secondary_pressure_target = cc.secondary_pressure_target
        ins.cascade_probability       = cc.cascade_probability
        ins.cascade_window_days       = cc.cascade_window_days
        ins.cascade_note              = cc.cascade_note
        ins.cascade_offset_note       = cc.cascade_offset_note

        delta = _CASCADE_W_DELTA.get(cc.cascade_state, 0)
        if delta > 0:
            ins.weight = (ins.weight or 0) + delta


def _enrich_resilience(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with resilience snapshot intelligence.
    Point-in-time assessment of operational shock absorption capacity.
    Must run AFTER _enrich_cascade() so cascade_state is available.
    """
    for ins in insights:
        rs = _compute_resilience(
            insight_category=ins.key,
            trajectory_state=ins.trajectory_state,
            trajectory_direction=ins.trajectory_direction,
            recovery_state=ins.recovery_state,
            recovery_probability=ins.recovery_probability,
            outcome_state=ins.outcome_state,
            reversibility_state=ins.reversibility_state,
            pressure_accumulation=ins.pressure_accumulation,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            signal_decay_state=ins.signal_decay_state,
            cascade_state=ins.cascade_state,
            obs_recovery_state=ins.obs_recovery_state,
            reversal_state=ins.reversal_state,
            timing_state=ins.timing_state,
            tradeoff_severity=ins.tradeoff_severity,
        )
        ins.resilience_state          = rs.resilience_state
        ins.absorption_capacity       = rs.absorption_capacity
        ins.weakest_operational_layer = rs.weakest_operational_layer
        ins.resilience_window         = rs.resilience_window
        ins.resilience_score          = rs.resilience_score
        ins.resilience_note           = rs.resilience_note


def _enrich_resilience_trajectory(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with resilience trajectory intelligence.
    Assesses how operational elasticity is evolving over time.
    Must run AFTER _enrich_resilience() so resilience snapshot fields are available.
    """
    for ins in insights:
        rt = _compute_resilience_traj(
            resilience_state=ins.resilience_state or "moderate",
            absorption_capacity=ins.absorption_capacity or "moderate",
            resilience_score=ins.resilience_score or 50,
            trajectory_state=ins.trajectory_state,
            trajectory_direction=ins.trajectory_direction,
            recovery_state=ins.recovery_state,
            outcome_state=ins.outcome_state,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            pressure_accumulation=ins.pressure_accumulation,
            reversibility_state=ins.reversibility_state,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            cascade_state=ins.cascade_state,
            obs_recovery_state=ins.obs_recovery_state,
            reversal_state=ins.reversal_state,
            timing_state=ins.timing_state,
        )
        ins.resilience_trajectory            = rt.resilience_trajectory
        ins.resilience_trajectory_velocity   = rt.resilience_trajectory_velocity
        ins.resilience_trajectory_note       = rt.resilience_trajectory_note
        ins.absorption_transition_note       = rt.absorption_transition_note
        ins.resilience_trajectory_confidence = rt.resilience_trajectory_confidence


def _enrich_adaptive_capacity(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with adaptive capacity intelligence.
    Assesses whether the system is becoming more or less capable of handling pressure.
    Must run AFTER _enrich_resilience_trajectory() so resilience trajectory is available.
    """
    for ins in insights:
        ac = _compute_adaptive_cap(
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            recovery_state=ins.recovery_state,
            recovery_probability=ins.recovery_probability,
            outcome_state=ins.outcome_state,
            pressure_accumulation=ins.pressure_accumulation,
            reversibility_state=ins.reversibility_state,
            obs_recovery_state=ins.obs_recovery_state,
            reversal_state=ins.reversal_state,
            timing_state=ins.timing_state,
            resilience_state=ins.resilience_state,
            resilience_trajectory=ins.resilience_trajectory,
            trajectory_direction=ins.trajectory_direction,
            cascade_state=ins.cascade_state,
        )
        ins.adaptive_capacity_state = ac.state
        ins.adaptation_direction    = ac.adaptation_direction
        ins.stabilization_trend     = ac.stabilization_trend
        ins.observability_trend     = ac.observability_trend
        ins.recurrence_trend        = ac.recurrence_trend
        ins.adaptation_note         = ac.adaptation_note
        ins.adaptation_confidence   = ac.adaptation_confidence
        ins.adaptation_cycles       = ac.adaptation_cycles


def _enrich_strategic_memory_drift(insights: list["InsightItem"]) -> None:
    """
    Mutate each InsightItem in-place with strategic memory drift intelligence.
    Detects divergence from historically effective recovery doctrine.
    Must run AFTER _enrich_adaptive_capacity() so adaptation signals are available.
    """
    for ins in insights:
        md = _compute_mem_drift(
            insight_category=ins.key,
            signal_lifecycle_stage=ins.signal_lifecycle_stage,
            signal_recurrence_count=ins.signal_recurrence_count,
            outcome_state=ins.outcome_state,
            recovery_state=ins.recovery_state,
            recovery_probability=ins.recovery_probability,
            adaptive_capacity_state=ins.adaptive_capacity_state,
            resilience_trajectory=ins.resilience_trajectory,
            reversal_state=ins.reversal_state,
            timing_state=ins.timing_state,
            obs_recovery_state=ins.obs_recovery_state,
            counterfactual_pressure_state=ins.counterfactual_pressure_state,
            pressure_accumulation=ins.pressure_accumulation,
            trajectory_direction=ins.trajectory_direction,
        )
        ins.strategic_drift_state   = md.drift_state
        ins.memory_continuity       = md.memory_continuity
        ins.doctrine_alignment_note = md.doctrine_alignment_note
        ins.repetition_pattern_note = md.repetition_pattern_note
        ins.drift_note              = md.drift_note
        ins.drift_confidence        = md.drift_confidence
        ins.historical_cycles       = md.historical_cycles


def _enrich_counterfactual(insights: list["InsightItem"], portfolio_patterns: list) -> None:
    """
    Mutate each InsightItem in-place with counterfactual pressure fields.
    Must run AFTER stabilization_lock enrichment.
    Applies weight delta for focus engine (only for recurring/persistent, not stale/fading).
    """
    for ins in insights:
        cf = _compute_cf(
            insight=ins,
            lifecycle=ins.signal_lifecycle_stage,
            trajectory_state=ins.trajectory_state,
            reversibility=ins.reversibility_state,
            decay=ins.signal_decay_state,
            recovery_state=ins.recovery_state,
            forecast_fragility=ins.forecast_fragility_state,
            portfolio_patterns=portfolio_patterns,
        )
        ins.counterfactual_pressure_state              = cf.pressure_state
        ins.counterfactual_transition_window_days      = cf.estimated_transition_window_days
        ins.counterfactual_reversibility_remaining_pct = cf.reversibility_remaining_pct
        ins.counterfactual_next_phase                  = cf.likely_next_phase
        ins.counterfactual_operational_time_pressure   = cf.operational_time_pressure
        ins.counterfactual_note                        = cf.counterfactual_note

        # Weight delta for focus priority — guard against stale/fading ghosts
        decay = ins.signal_decay_state
        lc    = ins.signal_lifecycle_stage
        if decay not in ("stale", "fading") and lc in ("recurring", "confirmed", "persistent"):
            delta = _CF_WEIGHT_DELTA.get(cf.pressure_state, 0.0)
            if delta > 0 and ins.weight is not None:
                ins.weight = max(0.0, min(100.0, ins.weight + delta))


def _enrich_comparisons(insights: list["InsightItem"], capacity_state: str) -> None:
    """
    Mutate each InsightItem with a PathComparisonOut when a two-path comparison exists.
    Must run after capacity enrichment (needs capacity_state) and after trajectory/lifecycle enrichment.
    """
    for ins in insights:
        pc = _compute_comparison(
            insight=ins,
            capacity_state=capacity_state,
            lifecycle=ins.signal_lifecycle_stage,
            trajectory=ins.trajectory_state,
            recovery_state=ins.recovery_state,
        )
        if pc is None:
            continue
        ins.path_comparison = PathComparisonOut(
            insight_key=pc.insight_key,
            path_a=ComparativePathOut(
                action_type=pc.path_a.action_type,
                stabilization_speed=pc.path_a.stabilization_speed,
                volatility_impact=pc.path_a.volatility_impact,
                observability_impact=pc.path_a.observability_impact,
                operator_load=pc.path_a.operator_load,
                reversibility_profile=pc.path_a.reversibility_profile,
                structural_depth=pc.path_a.structural_depth,
                path_note=pc.path_a.path_note,
            ),
            path_b=ComparativePathOut(
                action_type=pc.path_b.action_type,
                stabilization_speed=pc.path_b.stabilization_speed,
                volatility_impact=pc.path_b.volatility_impact,
                observability_impact=pc.path_b.observability_impact,
                operator_load=pc.path_b.operator_load,
                reversibility_profile=pc.path_b.reversibility_profile,
                structural_depth=pc.path_b.structural_depth,
                path_note=pc.path_b.path_note,
            ),
            contextual_note=pc.contextual_note,
            comparison_dimension=pc.comparison_dimension,
        )


def _trajectory_summary_line(insights: list["InsightItem"]) -> Optional[str]:
    """
    Build trajectory narrative for OperationalSummary.
    Only emitted when 2+ escalating/persistent insights AND recurring/systemic pressure.
    """
    escalating_or_persistent = [
        i for i in insights
        if getattr(i, "trajectory_state", None) in ("escalating", "persistent", "structurally_accumulating")
        and i.status not in ("resolved", "dismissed")
    ]
    if len(escalating_or_persistent) < 2:
        return None
    has_systemic = any(
        getattr(i, "signal_lifecycle_stage", None) == "recurring"
        or getattr(i, "pressure_accumulation", None) in ("accumulating", "compounding")
        for i in escalating_or_persistent
    )
    if not has_systemic:
        return None
    return (
        "Часть операционного давления продолжает накапливаться, "
        "несмотря на локальные стабилизации."
    )


def _enrich_sequencing(insights: list["InsightItem"], sequence: list["_SequencedActionDC"]) -> None:
    """Mutate each InsightItem in-place with sequencing fields."""
    seq_map = {s.insight_key: s for s in sequence}
    for ins in insights:
        s = seq_map.get(ins.key)
        if s is None:
            # Try prefix match for cases where key has extra segments
            cat = ins.key.split(":")[0]
            s = next((v for v in sequence if v.insight_key.split(":")[0] == cat), None)
        if s is not None:
            ins.sequence_stage                     = s.sequence_stage
            ins.stabilization_role                 = s.stabilization_role
            ins.expected_stabilization_window_days = s.expected_stabilization_window_days
            ins.unlocks_next_stage                 = s.unlocks_next_stage


def _feedback_summary_line(insights: list) -> Optional[str]:
    """
    Derives one optional line for the Operational Summary block based on outcome feedback bias.
    Only emits when evidence is clear: all-deprioritize or majority-reinforce.
    """
    deprioritized = [i for i in insights if getattr(i, "recommendation_confidence_delta", 0) == -12]
    reinforced    = [i for i in insights if getattr(i, "recommendation_confidence_delta", 0) == +10]

    if not insights:
        return None

    if deprioritized:
        cats = list({_normalize_cat(i.key) for i in deprioritized})
        if len(cats) == 1:
            _LABELS = {
                "seo_opportunity": "SEO",
                "high_ad_spend":   "рекламным ставкам",
                "margin_crisis":   "margin pressure",
                "low_stock":       "складским пополнениям",
                "high_rating":     "работе с отзывами",
                "sales_growth":    "масштабированию роста",
            }
            label = _LABELS.get(cats[0], cats[0].replace("_", " "))
            return f"Повторные интервенции по {label} больше не демонстрируют устойчивого эффекта."
        return "Повторяющиеся интервенции не приводят к устойчивой стабилизации."

    if reinforced and len(reinforced) >= len(insights) // 2:
        cats = list({_normalize_cat(i.key) for i in reinforced})
        if "seo_opportunity" in cats:
            age  = max((getattr(i, "signal_operational_age", 0) or 0 for i in reinforced), default=0)
            days = f"{age}+" if age > 0 else "18+"
            return f"SEO-интервенции сохраняют стабильность {days} дней."
        return "Операционные интервенции демонстрируют устойчивый эффект."

    return None


# ── Outcome feedback enrichment ───────────────────────────────────────────────

def _enrich_outcome_feedback(
    insights:     list["InsightItem"],
    notif_counts: dict[str, int],
) -> None:
    """
    Mutates each InsightItem in-place: sets outcome_feedback_* fields.
    Requires Sprint 24 signal_lifecycle_* already set.
    For deprioritize bias, prepends an alternative recommendation at position 0.
    """
    for ins in insights:
        cat = _normalize_cat(ins.key)
        fb = _eval_feedback(
            insight_type=cat,
            action_taken="accepted",  # conservative default; OperatorDecision lookup is future work
            outcome_state=ins.outcome_state,
            lifecycle_stage=ins.signal_lifecycle_stage,
            recurrence_count=ins.signal_recurrence_count or 0,
            notif_count=notif_counts.get(ins.key, 0),
        )
        ins.outcome_feedback_note           = fb.narrative
        ins.recommendation_confidence_delta = fb.confidence_delta
        ins.recommended_based_on_history    = fb.recommendation_bias == "reinforce"

        if fb.recommendation_bias == "deprioritize" and fb.alt_recommendation:
            recs = list(ins.recommendations)
            if recs and recs[0] != fb.alt_recommendation:
                ins.recommendations = [fb.alt_recommendation] + recs[1:]


# ── Signal age decay enrichment ───────────────────────────────────────────────

def _enrich_signal_decay(
    insights:        list["InsightItem"],
    notif_counts:    dict[str, int],
    first_seen_map:  dict[str, datetime] | None = None,
    resolved_history: dict[str, datetime] | None = None,
) -> None:
    """
    Mutates each InsightItem in-place: sets signal_decay_* fields.
    Applies confidence_penalty directly to decision_confidence_score (clamped [0, 100]).
    Requires Sprint 24 signal_lifecycle_stage already set.
    """
    _fs = first_seen_map or {}
    _rh = resolved_history or {}
    for ins in insights:
        cat = _normalize_cat(ins.key)
        dc = _compute_decay(
            insight_type=cat,
            lifecycle_stage=ins.signal_lifecycle_stage,
            first_detected=_fs.get(ins.key),
            last_confirmed=_rh.get(ins.key),
            recurrence_count=ins.signal_recurrence_count or 0,
            confidence_band=ins.decision_confidence_band,
        )
        ins.signal_decay_state   = dc.decay_state
        ins.signal_decay_penalty = dc.confidence_penalty
        ins.signal_decay_note    = dc.operational_note
        ins.signal_age_days      = dc.age_days
        # Apply penalty to confidence score in-place
        if ins.decision_confidence_score is not None and dc.confidence_penalty != 0:
            ins.decision_confidence_score = max(0, min(100, ins.decision_confidence_score + dc.confidence_penalty))


def _decay_summary_line(insights: list) -> Optional[str]:
    """
    Returns one optional line for the Operational Summary block about signal freshness.
    Priority: stale note over persistent note (more actionable).
    """
    stale      = [i for i in insights if getattr(i, "signal_decay_state", None) == "stale"]
    persistent = [i for i in insights if getattr(i, "signal_decay_state", None) == "persistent"]
    fading     = [i for i in insights if getattr(i, "signal_decay_state", None) == "fading"]

    if stale:
        return "Часть сигналов больше не подтверждается активной динамикой."
    if persistent and len(persistent) >= 2:
        return "Повторяющееся давление сохраняет устойчивый операционный паттерн."
    if fading:
        return "Часть операционного давления постепенно теряет подтверждение."
    return None


def _momentum_summary_line(insights: list) -> Optional[str]:
    """
    Sprint 30: Returns one optional narrative about focus momentum shift.
    Distinct from decay_summary_line — focuses on operational focus changes, not freshness.
    """
    historical = [i for i in insights if getattr(i, "signal_decay_state", None) == "stale"
                  and getattr(i, "signal_lifecycle_stage", None) != "recurring"]
    slowing    = [i for i in insights if getattr(i, "signal_decay_state", None) == "fading"]
    persistent = [i for i in insights if getattr(i, "signal_decay_state", None) == "persistent"
                  or getattr(i, "signal_lifecycle_stage", None) == "recurring"]

    if historical and persistent:
        return "Исторические сигналы больше не доминируют в операционном фокусе."
    if historical and slowing:
        return "Часть предыдущего давления постепенно теряет операционную активность."
    if persistent and len(persistent) >= 2 and not slowing:
        return "Повторяющиеся паттерны продолжают сохранять устойчивое давление."
    if slowing and not persistent:
        return "Часть предыдущего давления постепенно теряет операционную активность."
    return None


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
def _demo_response() -> InsightsResponse:
    items = [
        # ── demo-1: SEO — card is the bottleneck, not the product ─────────────
        InsightItem(
            id="demo-1", key="demo_seo_opportunity",
            type="warning", icon="⚠️",
            title="Карточка не конвертирует рекламный трафик в покупки",
            subtitle="Магнитные биты 6-13 мм · Wildberries",
            reasons=[
                "Эффективность рекламы: 4.9 ₽/₽ — на 43% ниже медианы категории (8.5 ₽/₽)",
                "Рейтинг 4.7 ★ и цена в диапазоне — продукт конкурентоспособен",
                "Причина потерь локализована на уровне карточки, не продукта",
                "Реклама активна 18 дн. — данных достаточно для вывода",
            ],
            recommendations=[
                "Переработать главный слайд: усилить product focus, убрать лишний текст",
                "Протестировать новый title с ключевым преимуществом в первых 5 словах",
                "Авто-пересборка применит стиль с лучшим win_rate в категории",
            ],
            confidence=74, confidence_level="medium",
            impact=InsightImpact(
                label="Потенциал при выходе на median ROAS",
                estimate="≈ 12k ₽/мес дополнительной выручки",
                sign="negative",
            ),
            benchmark=InsightBenchmark(
                metric="Выручка на ₽ рекламы (ROAS)",
                value="4.9 ₽/₽",
                baseline="median 8.5 ₽/₽ по категории",
                deviation="-43% ниже нормы",
            ),
            actions=[
                InsightAction(
                    label="Авто-пересборка", type="primary",
                    url="/dashboard/seo-cards",
                    params={"product": "Магнитные биты 6-13 мм", "category": "auto", "auto": "1"},
                ),
                InsightAction(label="SEO-карточки", type="secondary", url="/dashboard/seo-cards"),
            ],
            status="active", record_id=None,
            product_name="Магнитные биты 6-13 мм", product_sku="12345678",
            marketplace="wildberries", is_demo=True,
            impact_score=_impact_score(74, 12_000),
            estimated_monthly_loss_rub=12_000.0,
            automation_level="safe_auto",
            marketplace_mechanic="wb_indexation_cooldown",
            marketplace_risk_note=(
                "Частая смена описания может вызвать временную пессимизацию индексации WB. "
                "PULT применяет cooldown 72ч между пересборками одной карточки."
            ),
            sim_meta={"days_active": 18, "product_name": "Магнитные биты 6-13 мм"},
            marketplace_patterns=["reindexing_instability"],
            marketplace_behavior_note=(
                "На WB частые изменения контента карточки вызывают временную пессимизацию "
                "в поиске на 3–7 дн. PULT применяет cooldown 72ч между пересборками."
            ),
            marketplace_stabilization_window=7,
            outcome_state="improved",
            outcome_memory_note="SEO-пересборка ранее стабилизировала CTR. Эффект сохранялся 18 дн.",
            outcome_confidence=78,
        ),

        # ── demo-2: Ad spend — sustained degradation, not launch ramp-up ──────
        InsightItem(
            id="demo-2", key="demo_high_ad_spend",
            type="warning", icon="⚠️",
            title="Нагрузка рекламных расходов превышает устойчивый диапазон",
            subtitle="Блендер PowerBlend · Wildberries",
            reasons=[
                "ДРР: 34% — выше диапазона 10–14% в течение 21 дн.",
                "Рекламные расходы (34% ДРР) — основной драйвер давления на маржу",
                "Паттерн 21 дн. — не разовый выброс кампании, не рост выручки",
            ],
            recommendations=[
                "Проверить конверсию ключевых слов — часть трафика может быть нецелевой",
                "Скорректировать ставки на основе ROAS по каждому ключу",
                "Рассмотреть перераспределение бюджета на органический SEO-рост",
            ],
            confidence=88, confidence_level="high",
            impact=InsightImpact(
                label="Избыточная нагрузка",
                estimate="≈ 8k ₽/мес сверх устойчивого диапазона",
                sign="negative",
            ),
            benchmark=InsightBenchmark(
                metric="Доля рекламных расходов (ДРР)",
                value="34%",
                baseline="median 12% по категории",
                deviation="+183% выше нормы",
            ),
            actions=[
                InsightAction(label="Открыть рекламу", url="/auto-promotions", type="primary"),
                InsightAction(label="Финансы", url="/dashboard/finance", type="secondary"),
            ],
            status="active", record_id=None,
            product_name="Блендер PowerBlend", product_sku="11223344",
            marketplace="wildberries", is_demo=True,
            impact_score=_impact_score(88, 8_500),
            estimated_monthly_loss_rub=8_500.0,
            automation_level="human_required",
            marketplace_mechanic="wb_autoactions_margin_risk",
            marketplace_risk_note=(
                "WB autoactions ранее снижали маржу ниже безопасного порога. "
                "Изменение рекламных ставок требует ручного подтверждения."
            ),
            sim_meta={"days_active": 21, "ad_ratio_pct": 34.0, "margin_pct": 3.1, "product_name": "Блендер PowerBlend"},
            marketplace_patterns=["advertising_spike", "organic_recovery_lag"],
            marketplace_behavior_note=(
                "WB после резкого роста рекламных ставок обычно увеличивает CTR "
                "быстрее конверсии. Органический трафик перераспределяется через 10–15 дн."
            ),
            marketplace_stabilization_window=10,
            outcome_state="temporary",
            outcome_memory_note="Нагрузка вернулась спустя 24 дн. после корректировки ставок.",
            outcome_confidence=74,
        ),

        # ── demo-3: Margin crisis — causally linked to demo-2 (same product) ──
        InsightItem(
            id="demo-3", key="demo_margin_crisis",
            type="warning", icon="⚠️",
            title="Рекламные расходы опережают маржинальный потенциал товара",
            subtitle="Блендер PowerBlend · Wildberries",
            reasons=[
                "Маржа: 3.1% — разрыв 19 п.п. до медианы категории (22%)",
                "Рекламные расходы (34% ДРР) — основной драйвер давления на маржу",
                "Выручка стабильна 21 дн. — давление структурное, не сезонное",
            ],
            recommendations=[
                "Пересмотреть эффективность кампаний: снизить нецелевой трафик",
                "Рассмотреть повышение цены на 5–10% с тестом конверсии",
                "Сравнить экономику с аналогами категории",
            ],
            confidence=83, confidence_level="high",
            impact=InsightImpact(
                label="Потенциал при выходе на median",
                estimate="≈ +6.5k ₽/мес в месяц",
                sign="positive",
            ),
            benchmark=InsightBenchmark(
                metric="Маржинальность",
                value="3.1%",
                baseline="median 22% по категории",
                deviation="-19 п.п. ниже нормы",
            ),
            actions=[
                InsightAction(label="Финансы", url="/dashboard/finance", type="primary"),
                InsightAction(label="Калькулятор", url="/profit-calculator", type="secondary"),
            ],
            status="active", record_id=None,
            product_name="Блендер PowerBlend", product_sku="11223344",
            marketplace="wildberries", is_demo=True,
            impact_score=_impact_score(83, 6_500),
            estimated_monthly_gain_rub=6_500.0,
            automation_level="human_required",
            marketplace_mechanic="wb_autoactions_margin_risk",
            marketplace_risk_note=(
                "WB autoactions ранее снижали маржу ниже безопасного порога. "
                "Изменение цены или структуры затрат — только вручную."
            ),
            sim_meta={"days_active": 21, "margin_pct": 3.1, "pressure_source": "ad_driven", "product_name": "Блендер PowerBlend"},
            marketplace_patterns=["margin_pressure_ads"],
            marketplace_behavior_note=(
                "WB в конкурентных категориях быстро поднимает ДРР без пропорционального "
                "роста выручки. Структурное давление часто сопровождается высокой долей "
                "нецелевого трафика по широким ключам."
            ),
            marketplace_stabilization_window=14,
            outcome_state="repeated",
            outcome_memory_note="Структура затрат снова вышла за устойчивый диапазон спустя 21 дн.",
            outcome_confidence=76,
        ),

        # ── demo-4: Growth — confirmed across 3 periods, Ozon delay noted ─────
        InsightItem(
            id="demo-4", key="demo_sales_growth",
            type="positive", icon="📈",
            title="Рост подтверждён в 3 периода подряд (+28%)",
            subtitle="Ручной миксер ProMix · Ozon",
            reasons=[
                "Рост выручки +28% подтверждён в 3 независимых периодах",
                "Дисперсия: 0.18 — рост равномерный, не разовый всплеск",
                "Разовые пики исключены — паттерн устойчивый",
            ],
            recommendations=[
                "Масштабировать рекламный бюджет на этот товар",
                "Убедиться, что склад не опустеет при сохранении темпа",
                "Применить схему карточки к аналогичным товарам",
            ],
            confidence=82, confidence_level="high",
            impact=InsightImpact(
                label="Оценка устойчивого роста",
                estimate="≈ +9k ₽/мес при сохранении динамики",
                sign="positive",
            ),
            benchmark=None,
            actions=[
                InsightAction(label="Посмотреть финансы", url="/dashboard/finance", type="primary"),
            ],
            status="active", record_id=None,
            product_name="Ручной миксер ProMix", product_sku="87654321",
            marketplace="ozon", is_demo=True,
            impact_score=_impact_score(82, 9_600),
            estimated_monthly_gain_rub=9_600.0,
            automation_level="delayed",
            marketplace_mechanic="ozon_attribution_lag",
            marketplace_risk_note=(
                "Ozon аналитика имеет задержку атрибуции 24–48ч. "
                "Сигнал прошёл 48ч подтверждение — данные актуальны."
            ),
            sim_meta={"days_active": 12, "growth_pct": 28, "product_name": "Ручной миксер ProMix"},
            marketplace_patterns=["attribution_delay"],
            marketplace_behavior_note=(
                "На Ozon рост продаж часто подтверждается с задержкой атрибуции 24–48ч. "
                "Текущая динамика прошла период подтверждения — данные актуальны."
            ),
            marketplace_stabilization_window=2,
        ),
    ]
    _debug = settings.app_env != "production"
    chains, secondary_map = detect_operational_chains(items)
    for ins in items:
        if ins.id in secondary_map:
            ins.is_secondary = True
            ins.chain_id     = secondary_map[ins.id]
    scenarios = _collect_scenarios(items, secondary_map)
    # Sprint 22: portfolio patterns for demo (no resolved_history in demo)
    _pp_raw = _detect_portfolio([_insight_summary(i) for i in items])
    _pp = [PortfolioPattern(**vars(p)) for p in _pp_raw]
    # Sprint 23: decision confidence
    _enrich_decision_confidence(items, _pp_raw)
    # Sprint 24: signal lifecycle — hardcoded to showcase all stages
    _DEMO_LC: dict[str, tuple] = {
        "demo-1": ("confirmed",  "Паттерн наблюдается 18 дн. и подтверждён повторными наблюдениями. Операционное воздействие устойчиво.",  55, 18, 1),
        "demo-2": ("recurring",  "Паттерн повторяется после предыдущей стабилизации. Единичные меры не устраняют первопричину — требуется системное решение.", 85, 21, 3),
        "demo-3": ("recurring",  "Паттерн повторяется после предыдущей стабилизации. Единичные меры не устраняют первопричину — требуется системное решение.", 85, 21, 2),
        "demo-4": ("confirmed",  "Паттерн наблюдается 12 дн. и подтверждён повторными наблюдениями. Операционное воздействие устойчиво.",  55, 12, 1),
    }
    for ins in items:
        lc_data = _DEMO_LC.get(ins.id)
        if lc_data:
            ins.signal_lifecycle_stage   = lc_data[0]
            ins.signal_lifecycle_note    = lc_data[1]
            ins.signal_lifecycle_weight  = lc_data[2]
            ins.signal_operational_age   = lc_data[3]
            ins.signal_recurrence_count  = lc_data[4]
    # Sprint 26: outcome feedback — hardcoded to showcase all 4 bias outcomes
    _DEMO_FB: dict[str, tuple] = {
        # (outcome_feedback_note, recommendation_confidence_delta, recommended_based_on_history)
        "demo-1": ("SEO-пересборка ранее восстанавливала CTR этого товара.", +10, True),
        "demo-2": ("Снижение рекламной нагрузки ранее сопровождалось временным улучшением.", -6, False),
        "demo-3": ("Предыдущие изменения не устранили структурное давление на маржу.", -12, False),
        "demo-4": ("Исторических данных недостаточно для оценки эффекта.", 0, False),
    }
    for ins in items:
        fb_data = _DEMO_FB.get(ins.id)
        if fb_data:
            ins.outcome_feedback_note           = fb_data[0]
            ins.recommendation_confidence_delta = fb_data[1]
            ins.recommended_based_on_history    = fb_data[2]
            if fb_data[1] == -12 and ins.recommendations:
                alt = {
                    "demo-3": "Проверить ценовое позиционирование — предыдущие изменения не устранили структурное давление на маржу.",
                }.get(ins.id)
                if alt and ins.recommendations[0] != alt:
                    ins.recommendations = [alt] + list(ins.recommendations)[1:]
    # Sprint 27: signal age decay — hardcoded to showcase all states
    _DEMO_DECAY: dict[str, tuple] = {
        # (decay_state, confidence_penalty, operational_note, age_days)
        "demo-1": ("fresh",      0,   "Сигнал подтверждён недавней операционной динамикой.", 6),
        "demo-2": ("persistent", -3,  "Давление сохраняет устойчивый операционный паттерн.", 21),
        "demo-3": ("persistent", 0,   "Давление сохраняет устойчивый операционный паттерн.", 21),
        "demo-4": ("aging",      -4,  "Сигнал сохраняется без усиления давления.", 12),
    }
    for ins in items:
        dd = _DEMO_DECAY.get(ins.id)
        if dd:
            ins.signal_decay_state   = dd[0]
            ins.signal_decay_penalty = dd[1]
            ins.signal_decay_note    = dd[2]
            ins.signal_age_days      = dd[3]
            if ins.decision_confidence_score is not None and dd[1] != 0:
                ins.decision_confidence_score = max(0, min(100, ins.decision_confidence_score + dd[1]))
    # Sprint 33: operational trajectory — hardcoded demo values
    _DEMO_TRAJ: dict[str, tuple] = {
        # (state, direction, reversibility, window_days, accumulation, note)
        "demo-1": (
            "reversible", "stable", "easily_reversible", 10, "dissipating",
            "Давление остаётся обратимым при своевременном вмешательстве.",
        ),
        "demo-2": (
            "persistent", "stable", "conditionally_reversible", 21, "accumulating",
            "Давление устойчиво, но пока не нарастает.",
        ),
        "demo-3": (
            "escalating", "worsening", "narrowing_window", 21, "accumulating",
            "Нестабилизированная рекламная нагрузка продолжает усиливать давление на unit-экономику.",
        ),
        "demo-4": (
            "stabilizing", "improving", "easily_reversible", None, "dissipating",
            "Система демонстрирует признаки стабилизации.",
        ),
    }
    for ins in items:
        td = _DEMO_TRAJ.get(ins.id)
        if td:
            ins.trajectory_state          = td[0]
            ins.trajectory_direction      = td[1]
            ins.reversibility_state       = td[2]
            ins.stabilization_window_days = td[3]
            ins.pressure_accumulation     = td[4]
            ins.trajectory_note           = td[5]
    # Sprint 34: demo tradeoff — hardcoded per spec
    # (note, severity, duration_days, reversibility_profile, stabilization_benefit)
    _DEMO_TRADEOFF: dict[str, tuple] = {
        "demo-1": (
            "Пересмотр контента карточки вызывает кратковременную нестабильность индексации и позиций.",
            "mild", 10, "reversible",
            "Органический трафик восстанавливается на более высокой базе.",
        ),
        "demo-2": (
            "Снижение рекламной нагрузки временно замедляет оборот на период переходной оптимизации.",
            "mild", 10, "reversible",
            "Маржа стабилизируется. Unit-экономика восстанавливает устойчивость.",
        ),
        "demo-3": (
            "Корректировка цены временно нарушает CTR-сигнал до переиндексации карточки площадкой.",
            "moderate", 14, "reversible",
            "Unit-экономика выходит в устойчивую зону. Структурный дрейф останавливается.",
        ),
        # demo-4: no tradeoff (low_stock stabilization is straightforward replenishment)
    }
    for ins in items:
        dt = _DEMO_TRADEOFF.get(ins.id)
        if dt:
            ins.tradeoff_note          = dt[0]
            ins.tradeoff_severity      = dt[1]
            ins.tradeoff_duration_days = dt[2]
            ins.reversibility_profile  = dt[3]
            ins.stabilization_benefit  = dt[4]
    # Sprint 35: demo failure forecast — hardcoded per spec
    # (probability, fragility, next_stage, first_failure_mode, note, window_days)
    _DEMO_FORECAST: dict[str, tuple] = {
        "demo-1": (
            38, "sensitive", "advertising_dependency", None,
            "Давление пока остаётся обратимым, но окно стабилизации постепенно сужается.",
            21,
        ),
        "demo-2": (
            72, "fragile", "margin_crisis",
            "Платный трафик перестанет компенсировать рост ставок",
            "Рекламная нагрузка постепенно переходит в структурную зависимость от платного трафика.",
            14,
        ),
        "demo-3": (
            84, "critical", "structural_margin_compression",
            "Маржинальность перейдёт в отрицательный диапазон",
            "Маржинальная нагрузка достигла уровня, при котором структурная компрессия становится вероятной без активного вмешательства.",
            None,
        ),
        "demo-4": (
            29, "stable", None, None,
            "Текущее давление остаётся управляемым при своевременном вмешательстве.",
            None,
        ),
    }
    for ins in items:
        df = _DEMO_FORECAST.get(ins.id)
        if df:
            ins.forecast_escalation_probability  = df[0]
            ins.forecast_fragility_state         = df[1]
            ins.forecast_next_stage              = df[2]
            ins.forecast_first_failure_mode      = df[3]
            ins.forecast_note                    = df[4]
            ins.forecast_instability_window_days = df[5]
    # Sprint 36: demo recovery paths — hardcoded per spec
    # (probability, state, first_recovered, lagging, window_days, note, dependency)
    _DEMO_RECOVERY: dict[str, tuple] = {
        "demo-1": (
            82, "quick", "CTR", "органическая позиция", 14,
            "После стабилизации карточки CTR обычно восстанавливается раньше органической позиции.",
            "stable indexing period",
        ),
        "demo-2": (
            58, "gradual", "рекламный расход", "стабильность ROAS", 21,
            "Рекламная эффективность обычно нормализуется раньше полной стабилизации ROAS.",
            "unit-экономика без дефицита маржи",
        ),
        "demo-3": (
            34, "structural", "вклад в маржу", "стабильность повторных покупок", None,
            "Текущее давление редко стабилизируется без пересмотра закупочной или ценовой модели.",
            "пересмотр ценовой модели",
        ),
        "demo-4": (
            61, "unstable", "скорость продаж", "стабильность атрибуции", 30,
            "Даже после частичной стабилизации паттерн может возвращаться при росте нагрузки.",
            "баланс складского буфера",
        ),
    }
    for ins in items:
        dr = _DEMO_RECOVERY.get(ins.id)
        if dr:
            ins.recovery_probability          = dr[0]
            ins.recovery_state                = dr[1]
            ins.first_recovered_metric        = dr[2]
            ins.lagging_metric                = dr[3]
            ins.expected_recovery_window_days = dr[4]
            ins.recovery_note                 = dr[5]
            ins.recovery_dependency           = dr[6]
    # Sprint 30: build focus AFTER all enrichments so decay+lifecycle are set on items
    focus = _build_focus(items, chains, scenarios)
    # Sprint 25: operational intelligence summary
    _op_summary = _make_op_summary(items, _pp_raw, {}, 0.0, 0.0)
    # Sprint 32: demo sequencing — hardcoded per spec
    _DEMO_SEQ = [
        _SequencedActionDC(
            insight_key="demo_high_ad_spend",
            sequence_stage=1, sequence_priority=1,
            stabilization_role="fast_stabilization",
            expected_stabilization_window_days=14,
            unlocks_next_stage=True,
            dependency_reduction=["давление на маржу", "зависимость от рекламы"],
            sequencing_confidence="stable",
            sequencing_note="Стабилизация рекламной нагрузки может снизить давление на маржу.",
            insight_title="Рекламная нагрузка выше нормы",
        ),
        _SequencedActionDC(
            insight_key="demo_margin_crisis",
            sequence_stage=2, sequence_priority=2,
            stabilization_role="structural_fix",
            expected_stabilization_window_days=21,
            unlocks_next_stage=False,
            dependency_reduction=["операционный дрейф"],
            sequencing_confidence="moderate",
            sequencing_note="Имеет смысл после стабилизации рекламной нагрузки.",
            insight_title="Маржа ниже устойчивого диапазона",
        ),
        _SequencedActionDC(
            insight_key="demo_seo_opportunity",
            sequence_stage=1, sequence_priority=3,
            stabilization_role="parallel_track",
            expected_stabilization_window_days=21,
            unlocks_next_stage=False,
            dependency_reduction=[],
            sequencing_confidence="low",
            sequencing_note="Обычно выполняется на раннем этапе стабилизации.",
            insight_title="Карточка не конвертирует рекламный трафик",
        ),
    ]
    _enrich_sequencing(items, _DEMO_SEQ)
    _demo_seq_out = [SequencedActionOut(**_dc.asdict(s)) for s in _DEMO_SEQ]
    if _op_summary:
        _op_summary.sequencing_summary_line = _seq_summary_line(_DEMO_SEQ)
        _op_summary.trajectory_summary_line = _trajectory_summary_line(items)
    # Sprint 37: demo capacity — computed from enriched demo items
    _demo_cap_dc = _compute_capacity(items, _pp_raw, fatigue_score=0.4, stability_credit=0.0)
    # Sprint 38: demo stabilization lock — computed from enriched demo items
    _enrich_stabilization_lock(items, _demo_cap_dc.capacity_state)
    # Sprint 39: demo counterfactual — hardcoded per spec
    # (pressure_state, transition_window_days, reversibility_pct, next_phase, time_pressure, note)
    _DEMO_CF: dict[str, tuple] = {
        "demo-1": (
            "stable", 14, 82, None, "low",
            "Система пока сохраняет высокую операционную гибкость.",
        ),
        "demo-2": (
            "narrowing", 21, 61,
            "Снижение эффективности рекламных расходов", "moderate",
            "Окно стабилизации постепенно сужается по мере накопления давления.",
        ),
        "demo-3": (
            "accelerating", 21, 34,
            "Структурное сжатие unit-экономики", "elevated",
            "При сохранении текущей динамики сигнал обычно переходит в следующую фазу в течение ближайших недель.",
        ),
        "demo-4": (
            "stable", 10, 76, None, "low",
            "Система пока сохраняет высокую операционную гибкость.",
        ),
    }
    for ins in items:
        dc = _DEMO_CF.get(ins.id)
        if dc:
            ins.counterfactual_pressure_state              = dc[0]
            ins.counterfactual_transition_window_days      = dc[1]
            ins.counterfactual_reversibility_remaining_pct = dc[2]
            ins.counterfactual_next_phase                  = dc[3]
            ins.counterfactual_operational_time_pressure   = dc[4]
            ins.counterfactual_note                        = dc[5]
    # Sprint 42: demo comparisons — computed from enriched demo items (uses key lookup + context)
    _enrich_comparisons(items, _demo_cap_dc.capacity_state)
    # Sprint 44: demo observability recovery — computed from enriched demo items
    _enrich_observability_recovery(items)
    # Sprint 48: demo intervention timing — computed then overridden by spec
    _enrich_timing(items, "active")
    _DEMO_TIMING: dict[str, tuple] = {
        # (timing_state, readiness, timing_note, window_days, premature_risk, premature_note, delayed_risk, delayed_note, waiting_benefit, readiness_condition)
        "demo-1": (
            "stabilization_phase", "nearly_ready",
            "Операционная динамика постепенно стабилизируется, но часть эффектов ещё формируется.",
            10, "low", None, "low", None, None, None,
        ),
        "demo-2": (
            "observation_phase", "unstable",
            "Система продолжает отделять эффект предыдущих изменений от текущего давления.",
            14, "high", "Раннее вмешательство может дополнительно исказить наблюдаемость системы.",
            "low", None, "Дополнительное время наблюдения повысит точность следующего решения.",
            "После завершения окна наблюдения",
        ),
        "demo-3": (
            "narrowing_window", "elevated",
            "Часть операционной гибкости постепенно сужается — окно стабилизации продолжает сокращаться.",
            13, "low", None, "high",
            "Дальнейшее ожидание может увеличить стоимость последующей стабилизации.",
            None, None,
        ),
        "demo-4": (
            "emerging_window", "monitor",
            "Операционное окно постепенно формируется — наблюдение подтверждает развитие сигнала.",
            14, "moderate", None, "moderate", None, None, None,
        ),
    }
    for ins in items:
        dt = _DEMO_TIMING.get(ins.id)
        if dt:
            ins.timing_state                = dt[0]
            ins.intervention_readiness      = dt[1]
            ins.timing_note                 = dt[2]
            ins.optimal_window_days         = dt[3]
            ins.premature_intervention_risk = dt[4]
            ins.premature_risk_note         = dt[5]
            ins.delayed_intervention_risk   = dt[6]
            ins.delayed_risk_note           = dt[7]
            ins.waiting_benefit             = dt[8]
            ins.readiness_condition         = dt[9]
    # Sprint 45: demo opportunity cost — derived from enriched demo fields
    _enrich_opportunity_cost(items)
    # Sprint 49: demo intervention reversal — computed then overridden by spec
    _enrich_reversal(items)
    # (state, probability, window_days, trigger, note, rollback_safety, rollback_effect, stabilization_dependency)
    _DEMO_REVERSAL: dict[str, tuple] = {
        "demo-1": (
            "stable_intervention", 18, None, None, None,
            "conditional", None, None,
        ),
        "demo-2": (
            "diminishing_return", 48, 14,
            "Рекламная нагрузка продолжает накапливаться без пропорционального улучшения конверсии",
            "Текущее вмешательство постепенно приближается к фазе снижающейся отдачи.",
            "conditional", None, None,
        ),
        "demo-3": (
            "overextended", 72, 7,
            "Структурное давление продолжает нарастать, несмотря на вмешательства",
            "Дальнейшее усиление может повысить операционную волатильность.",
            "risky",
            "Возможна временная просадка оборота на этапе структурной стабилизации.",
            None,
        ),
        "demo-4": (
            "reversal_window", 75, 14, None,
            "Появилось окно для безопасного ослабления текущего вмешательства.",
            "safe",
            "Темп роста может временно замедлиться в процессе стабилизации.",
            None,
        ),
    }
    for ins in items:
        dr = _DEMO_REVERSAL.get(ins.id)
        if dr:
            ins.reversal_state              = dr[0]
            ins.reversal_probability        = dr[1]
            ins.reversal_window_days        = dr[2]
            ins.reversal_trigger            = dr[3]
            ins.reversal_note               = dr[4]
            ins.rollback_safety             = dr[5]
            ins.rollback_effect_expectation = dr[6]
            ins.stabilization_dependency    = dr[7]
    # Sprint 50: demo secondary pressure cascade — computed then overridden by spec
    _enrich_cascade(items)
    # (state, direction, secondary_pressure_target, probability, window_days, note, offset_note)
    _DEMO_CASCADE: dict[str, tuple] = {
        "demo-1": (
            "isolated", "localized",
            None,
            22, None, None, None,
        ),
        "demo-2": (
            "shifting_pressure", "adjacent",
            "unit-экономику",
            48, 14,
            "Коррекция рекламного бюджета вероятно затронет показатели органической видимости.",
            "Операционная миграция давления ожидается в горизонте ≈14 дней.",
        ),
        "demo-3": (
            "coupled_instability", "expanding",
            "ценовое позиционирование",
            71, 10,
            "Нестабильность в unit-экономике создаёт связанное операционное давление на смежные категории.",
            "Смещение давления вероятно начнёт проявляться в течение ≈10 дней.",
        ),
        "demo-4": (
            "isolated", "localized",
            None,
            18, None, None, None,
        ),
    }
    for ins in items:
        dc = _DEMO_CASCADE.get(ins.id)
        if dc:
            ins.cascade_state             = dc[0]
            ins.cascade_direction         = dc[1]
            ins.secondary_pressure_target = dc[2]
            ins.cascade_probability       = dc[3]
            ins.cascade_window_days       = dc[4]
            ins.cascade_note              = dc[5]
            ins.cascade_offset_note       = dc[6]
    # Sprint 51: demo resilience snapshot — computed then overridden by spec
    _enrich_resilience(items)
    # (state, absorption_capacity, weakest_layer, window, score, note)
    _DEMO_RESILIENCE: dict[str, tuple] = {
        "demo-1": (
            "resilient", "moderate",
            "SEO-видимость", 21, 62,
            "Операционная устойчивость сохраняется на достаточном уровне для стабилизации.",
        ),
        "demo-2": (
            "narrowing", "narrowing",
            "рекламная эффективность", 10, 36,
            "Способность системы поглощать давление постепенно снижается.",
        ),
        "demo-3": (
            "brittle", "narrowing",
            "маржинальная устойчивость", 7, 22,
            "Система находится в зоне повышенной операционной хрупкости.",
        ),
        "demo-4": (
            "resilient", "high",
            "маржинальная модель", 21, 70,
            "Операционная устойчивость сохраняется на достаточном уровне для стабилизации.",
        ),
    }
    for ins in items:
        dr = _DEMO_RESILIENCE.get(ins.id)
        if dr:
            ins.resilience_state          = dr[0]
            ins.absorption_capacity       = dr[1]
            ins.weakest_operational_layer = dr[2]
            ins.resilience_window         = dr[3]
            ins.resilience_score          = dr[4]
            ins.resilience_note           = dr[5]
    # Sprint 52: demo resilience trajectory — computed then overridden by spec
    _enrich_resilience_trajectory(items)
    # (trajectory, velocity, note, absorption_transition_note, confidence)
    _DEMO_RESILIENCE_TRAJ: dict[str, tuple] = {
        "demo-1": (
            "stabilizing", None,
            "Система сохраняет текущий уровень устойчивости без признаков ускоряющейся деградации.",
            None, 50,
        ),
        "demo-2": (
            "degrading", "gradual",
            "За последние циклы способность системы стабилизировать давление постепенно снижается.",
            "Способность системы поглощать давление сузилась — вероятно с moderate до narrowing.",
            65,
        ),
        "demo-3": (
            "structurally_degrading", "accelerating",
            "Несколько операционных слоёв одновременно теряют способность к самостоятельной стабилизации.",
            "Абсорбционный ресурс системы приближается к операционному пределу.",
            82,
        ),
        "demo-4": (
            "recovering", None,
            "Операционная устойчивость постепенно восстанавливает способность абсорбировать давление.",
            "После периода давления система постепенно вернулась к high absorption.",
            72,
        ),
    }
    for ins in items:
        dt = _DEMO_RESILIENCE_TRAJ.get(ins.id)
        if dt:
            ins.resilience_trajectory            = dt[0]
            ins.resilience_trajectory_velocity   = dt[1]
            ins.resilience_trajectory_note       = dt[2]
            ins.absorption_transition_note       = dt[3]
            ins.resilience_trajectory_confidence = dt[4]
    # Sprint 53: demo adaptive capacity — computed then overridden by spec
    _enrich_adaptive_capacity(items)
    # (state, adaptation_direction, stabilization_trend, observability_trend, recurrence_trend, note, confidence, cycles)
    _DEMO_ADAPTIVE: dict[str, tuple] = {
        "demo-1": (
            "plateauing", "plateauing",
            "без изменений", None, None,
            "Операционная адаптация сохраняется на стабильном уровне без дальнейшего ускорения восстановления.",
            55, 2,
        ),
        "demo-2": (
            "rigid", "constrained",
            "не улучшается", "фрагментируется", "нарастает",
            "Повторяющиеся циклы давления требуют сопоставимого объёма стабилизации без признаков ускорения адаптации.",
            72, 3,
        ),
        "demo-3": (
            "deteriorating", "declining",
            "увеличивается", "деградирует", "нарастает",
            "Некоторые повторяющиеся циклы начинают требовать большего времени и ресурса на стабилизацию.",
            82, 4,
        ),
        "demo-4": (
            "strengthening", "improving",
            "сокращается", "улучшается", "снижается",
            "Система постепенно проходит повторное давление легче.",
            76, 3,
        ),
    }
    for ins in items:
        da = _DEMO_ADAPTIVE.get(ins.id)
        if da:
            ins.adaptive_capacity_state = da[0]
            ins.adaptation_direction    = da[1]
            ins.stabilization_trend     = da[2]
            ins.observability_trend     = da[3]
            ins.recurrence_trend        = da[4]
            ins.adaptation_note         = da[5]
            ins.adaptation_confidence   = da[6]
            ins.adaptation_cycles       = da[7]
    # Sprint 54: demo strategic memory drift — computed then overridden by spec
    _enrich_strategic_memory_drift(items)
    # (drift_state, memory_continuity, doctrine_alignment_note, repetition_pattern_note, drift_note, confidence, historical_cycles)
    _DEMO_MEM_DRIFT: dict[str, tuple] = {
        "demo-1": (
            "drifting", "partially_connected",
            "Стабилизационная траектория начинает расходиться с ранее успешными операционными сценариями.",
            None,
            "Текущая стабилизация постепенно расходится с ранее устойчивыми сценариями восстановления.",
            60, 2,
        ),
        "demo-2": (
            "fragmented", "fragmented",
            "Исторически устойчивые сценарии стабилизации утрачивают преемственность.",
            "Рекламная нагрузка продолжает воспроизводить предыдущие циклы без коррекции операционного подхода.",
            "Повторяющиеся циклы стабилизации начинают использовать несовместимые операционные сценарии.",
            72, 3,
        ),
        "demo-3": (
            "compounding_repetition", "disconnected",
            "Повторяющиеся циклы воспроизводят ранее неустойчивые сценарии без признаков адаптации.",
            "Цикл маржинального давления повторяется без улучшения структурной устойчивости.",
            "Система воспроизводит ранее неустойчивые сценарии восстановления без признаков адаптации.",
            85, 4,
        ),
        "demo-4": (
            "aligned", "connected",
            "Текущий подход к стабилизации соотносится с ранее эффективными сценариями восстановления.",
            None,
            "Стабилизационная траектория сохраняет преемственность с ранее устойчивыми сценариями восстановления.",
            55, 3,
        ),
    }
    for ins in items:
        dm = _DEMO_MEM_DRIFT.get(ins.id)
        if dm:
            ins.strategic_drift_state   = dm[0]
            ins.memory_continuity       = dm[1]
            ins.doctrine_alignment_note = dm[2]
            ins.repetition_pattern_note = dm[3]
            ins.drift_note              = dm[4]
            ins.drift_confidence        = dm[5]
            ins.historical_cycles       = dm[6]
    _demo_cap_out = OperationalCapacityOut(
        capacity_state=_demo_cap_dc.capacity_state,
        operational_bandwidth_score=_demo_cap_dc.operational_bandwidth_score,
        overload_risk=_demo_cap_dc.overload_risk,
        defer_categories=_demo_cap_dc.defer_categories,
        capacity_note=_demo_cap_dc.capacity_note,
    )
    # Sprint 40: demo operator strategy profile — hardcoded per spec
    _demo_strategy = OperatorStrategyProfileOut(
        intervention_style="reactive",
        pacing_discipline="moderate",
        recovery_patience="unstable",
        structural_decision_tendency="symptom_focused",
        operational_volatility_source="mixed",
        strategic_stability_score=58,
        stability_band="generally_stable",
        coaching_note="Система замечает склонность к ранним вмешательствам до завершения стабилизационного окна.",
        profile_confidence="moderate",
    )
    # Sprint 43: demo strategy commitment — per spec: demo-2+demo-3 → active structural_margin_recovery
    _demo_commitment = StrategyCommitmentOut(
        strategy_type="structural_margin_recovery",
        commitment_state="active",
        interruption_risk="moderate",
        observability_quality="sufficient",
        commitment_score=72,
        commitment_note="Текущий stabilization cycle ориентирован на структурное восстановление unit-экономики.",
        estimated_observation_window_days=14,
    )
    # Sprint 55: demo operational regime — derived from enriched demo items
    _demo_regime_dc = _compute_regime(
        insights=items,
        commitment_state=_demo_commitment.commitment_state,
        capacity_state=_demo_cap_dc.capacity_state,
    )
    _demo_regime_out = OperationalRegimeOut(
        regime=_demo_regime_dc.regime,
        regime_direction=_demo_regime_dc.regime_direction,
        operational_posture=_demo_regime_dc.operational_posture,
        resilience_context=_demo_regime_dc.resilience_context,
        intervention_tolerance=_demo_regime_dc.intervention_tolerance,
        observability_quality=_demo_regime_dc.observability_quality,
        regime_note=_demo_regime_dc.regime_note,
        regime_confidence=_demo_regime_dc.regime_confidence,
    )
    # Sprint 56: demo decision energy — derived from enriched demo items
    _demo_energy_dc = _compute_energy(
        insights=items,
        capacity_state=_demo_cap_dc.capacity_state,
        regime=_demo_regime_dc.regime,
    )
    _demo_energy_out = DecisionEnergyOut(
        energy_state=_demo_energy_dc.energy_state,
        coordination_load=_demo_energy_dc.coordination_load,
        observability_load=_demo_energy_dc.observability_load,
        stabilization_burden=_demo_energy_dc.stabilization_burden,
        execution_complexity=_demo_energy_dc.execution_complexity,
        energy_note=_demo_energy_dc.energy_note,
        energy_confidence=_demo_energy_dc.energy_confidence,
    )
    # Sprint 57: demo operational phase transition — derived from enriched demo items
    _demo_phase_dc = _compute_phase(
        insights=items,
        regime=_demo_regime_dc.regime,
        capacity_state=_demo_cap_dc.capacity_state,
        energy_state=_demo_energy_dc.energy_state,
    )
    _demo_phase_out = OperationalPhaseTransitionOut(
        phase=_demo_phase_dc.phase,
        transition_direction=_demo_phase_dc.transition_direction,
        transition_velocity=_demo_phase_dc.transition_velocity,
        transition_stability=_demo_phase_dc.transition_stability,
        transition_driver=_demo_phase_dc.transition_driver,
        phase_note=_demo_phase_dc.phase_note,
        phase_confidence=_demo_phase_dc.phase_confidence,
    )
    # Sprint 58: demo stability topology — derived from enriched demo items
    _demo_topo_dc = _compute_topology(
        insights=items,
        regime=_demo_regime_dc.regime,
        capacity_state=_demo_cap_dc.capacity_state,
        energy_state=_demo_energy_dc.energy_state,
        phase=_demo_phase_dc.phase,
    )
    _demo_topo_out = StabilityTopologyOut(
        topology_state=_demo_topo_dc.topology_state,
        dominant_stability_layer=_demo_topo_dc.dominant_stability_layer,
        weakest_stability_layer=_demo_topo_dc.weakest_stability_layer,
        compensation_behavior=_demo_topo_dc.compensation_behavior,
        structural_balance=_demo_topo_dc.structural_balance,
        remaining_flexibility=_demo_topo_dc.remaining_flexibility,
        topology_note=_demo_topo_dc.topology_note,
        topology_confidence=_demo_topo_dc.topology_confidence,
    )
    # Sprint 59: demo operational doctrine — derived from enriched demo items
    _demo_doctrine_dc = _compute_doctrine(
        insights=items,
        regime=_demo_regime_dc.regime,
        phase=_demo_phase_dc.phase,
        topology_state=_demo_topo_dc.topology_state,
        energy_state=_demo_energy_dc.energy_state,
    )
    _demo_doctrine_out = OperationalDoctrineOut(
        doctrine_state=_demo_doctrine_dc.doctrine_state,
        doctrine_pattern=_demo_doctrine_dc.doctrine_pattern,
        adaptation_mode=_demo_doctrine_dc.adaptation_mode,
        institutionalization_level=_demo_doctrine_dc.institutionalization_level,
        doctrine_flexibility=_demo_doctrine_dc.doctrine_flexibility,
        doctrine_note=_demo_doctrine_dc.doctrine_note,
        doctrine_confidence=_demo_doctrine_dc.doctrine_confidence,
    )
    # Sprint 60: demo institutional inertia — derived from enriched demo items
    _demo_inertia_dc = _compute_inertia(
        insights=items,
        regime=_demo_regime_dc.regime,
        phase=_demo_phase_dc.phase,
        topology_state=_demo_topo_dc.topology_state,
        energy_state=_demo_energy_dc.energy_state,
        doctrine_state=_demo_doctrine_dc.doctrine_state,
    )
    _demo_inertia_out = InstitutionalInertiaOut(
        inertia_state=_demo_inertia_dc.inertia_state,
        adaptation_resistance=_demo_inertia_dc.adaptation_resistance,
        behavioral_repeatability=_demo_inertia_dc.behavioral_repeatability,
        structural_elasticity=_demo_inertia_dc.structural_elasticity,
        recovery_mobility=_demo_inertia_dc.recovery_mobility,
        inertia_driver=_demo_inertia_dc.inertia_driver,
        inertia_window_days=_demo_inertia_dc.inertia_window_days,
        inertia_note=_demo_inertia_dc.inertia_note,
        inertia_confidence=_demo_inertia_dc.inertia_confidence,
    )
    # Sprint 61: demo structural recovery capacity — derived from enriched demo items
    _demo_recovery_cap_dc = _compute_recovery_cap(
        insights=items,
        regime=_demo_regime_dc.regime,
        phase=_demo_phase_dc.phase,
        topology_state=_demo_topo_dc.topology_state,
        energy_state=_demo_energy_dc.energy_state,
        doctrine_state=_demo_doctrine_dc.doctrine_state,
        inertia_state=_demo_inertia_dc.inertia_state,
    )
    _demo_recovery_cap_out = StructuralRecoveryCapacityOut(
        recovery_state=_demo_recovery_cap_dc.recovery_state,
        structural_recoverability=_demo_recovery_cap_dc.structural_recoverability,
        recovery_elasticity=_demo_recovery_cap_dc.recovery_elasticity,
        restructuring_requirement=_demo_recovery_cap_dc.restructuring_requirement,
        continuity_dependence=_demo_recovery_cap_dc.continuity_dependence,
        structural_recovery_horizon=_demo_recovery_cap_dc.structural_recovery_horizon,
        recovery_window_days=_demo_recovery_cap_dc.recovery_window_days,
        structural_reversibility_index=_demo_recovery_cap_dc.structural_reversibility_index,
        recovery_capacity_note=_demo_recovery_cap_dc.recovery_capacity_note,
        recovery_capacity_confidence=_demo_recovery_cap_dc.recovery_capacity_confidence,
    )
    # Sprint 47: demo decision drift — derived from enriched demo items
    _demo_drift_dc = _compute_drift(
        insights=items,
        commitment_state=_demo_commitment.commitment_state,
        commitment_shift_type=None,
        interruption_risk=_demo_commitment.interruption_risk,
        observability_quality=_demo_commitment.observability_quality,
        intervention_style=_demo_strategy.intervention_style,
        pacing_discipline=_demo_strategy.pacing_discipline,
    )
    _demo_drift_out = DecisionDriftOut(
        drift_state=_demo_drift_dc.drift_state,
        drift_note=_demo_drift_dc.drift_note,
        intervention_overlap=_demo_drift_dc.intervention_overlap,
        sequencing_continuity=_demo_drift_dc.sequencing_continuity,
        observation_reset_count=_demo_drift_dc.observation_reset_count,
    )
    return InsightsResponse(
        insights=items, is_demo=True,
        focused_insights=_focused_filter(items, include_debug=_debug),
        operational_chains=chains,
        operational_scenarios=scenarios,
        operational_focus=focus,
        portfolio_patterns=_pp,
        operational_summary=_op_summary,
        stabilization_sequence=_demo_seq_out,
        operational_capacity=_demo_cap_out,
        operator_strategy_profile=_demo_strategy,
        strategy_commitment=_demo_commitment,
        decision_drift=_demo_drift_out,
        operational_regime=_demo_regime_out,
        decision_energy=_demo_energy_out,
        operational_phase_transition=_demo_phase_out,
        stability_topology=_demo_topo_out,
        operational_doctrine=_demo_doctrine_out,
        institutional_inertia=_demo_inertia_out,
        structural_recovery_capacity=_demo_recovery_cap_out,
        total_active=len(items), has_data=False,
        total_warnings=3, total_positive=1,
        estimated_monthly_loss=27_000.0,
    )


# ── Insight computation ────────────────────────────────────────────────────────

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
                        label="Авто-пересборка", type="primary",
                        url="/dashboard/seo-cards",
                        params={"product": title, "category": "auto", "auto": "1"},
                    ),
                    InsightAction(label="SEO-карточки", type="secondary", url="/dashboard/seo-cards"),
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
                        InsightAction(label="Открыть рекламу", url="/auto-promotions", type="primary"),
                        InsightAction(label="Финансы", url="/dashboard/finance", type="secondary"),
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
                        InsightAction(label="Финансы", url="/dashboard/finance", type="primary"),
                        InsightAction(label="Калькулятор", url="/profit-calculator", type="secondary"),
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
                    InsightAction(label="Финансы", url="/dashboard/finance", type="primary"),
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
                        label="Авто-пересборка карточки", type="primary",
                        url="/dashboard/seo-cards",
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

@router.get("/insights", response_model=InsightsResponse)
async def get_insights(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uid = current_user.id

    s_res = await db.execute(
        select(InsightRecord).where(InsightRecord.user_id == uid)
    )
    all_records = s_res.scalars().all()
    statuses: dict[str, tuple[str, str]] = {
        r.insight_key: (r.status, r.id) for r in all_records
    }
    resolved_history: dict[str, datetime] = {
        r.insight_key: r.updated_at
        for r in all_records if r.status == "resolved" and r.updated_at
    }

    # Notification recurrence counts (90-day window)
    cutoff_90 = datetime.utcnow() - timedelta(days=90)
    nc_res = await db.execute(
        select(TelegramNotificationLog.notification_key, sa_func.count().label("cnt"))
        .where(
            TelegramNotificationLog.user_id == uid,
            TelegramNotificationLog.notification_key.like("insight:%"),
            TelegramNotificationLog.sent_at >= cutoff_90,
        )
        .group_by(TelegramNotificationLog.notification_key)
    )
    notif_counts: dict[str, int] = {
        row.notification_key.replace("insight:", "", 1): row.cnt
        for row in nc_res
    }

    # Most recent measured SEO rebuild outcome per product
    rb_res = await db.execute(
        select(SeoRebuild)
        .where(SeoRebuild.user_id == uid, SeoRebuild.delta_ctr_percent.isnot(None))
        .order_by(SeoRebuild.measured_at.desc())
        .limit(100)
    )
    rebuild_outcomes: dict[str, SeoRebuild] = {}
    for rb in rb_res.scalars():
        if rb.product_name and rb.product_name not in rebuild_outcomes:
            rebuild_outcomes[rb.product_name] = rb

    # Sprint 24: first-seen timestamps from notification log (Telegram maturity gate)
    fd_res = await db.execute(
        select(
            TelegramNotificationLog.notification_key,
            sa_func.min(TelegramNotificationLog.sent_at).label("first_at"),
        )
        .where(
            TelegramNotificationLog.user_id == uid,
            TelegramNotificationLog.notification_key.like("first_detected:%"),
        )
        .group_by(TelegramNotificationLog.notification_key)
    )
    first_seen_map: dict[str, datetime] = {
        row.notification_key.replace("first_detected:", "", 1): row.first_at
        for row in fd_res
    }

    insights = await _compute_insights(
        uid, db, statuses,
        resolved_history=resolved_history,
        notif_counts=notif_counts,
        rebuild_outcomes=rebuild_outcomes,
    )

    if not insights:
        # Step 3: NO fabricated demo in the data-path. Distinguish no-imports
        # (onboarding) from imports-present-but-no-signal (healthy/no action).
        has_imports = (await db.execute(
            select(sa_func.count()).select_from(ImportedFinanceRow)
            .where(ImportedFinanceRow.user_id == uid)
        )).scalar() or 0
        if not has_imports:
            has_imports = (await db.execute(
                select(sa_func.count()).select_from(ImportedProductRow)
                .where(ImportedProductRow.user_id == uid)
            )).scalar() or 0
        return InsightsResponse(
            insights=[], focused_insights=[],
            is_demo=False,
            total_active=0, has_data=bool(has_imports),
            total_warnings=0, total_positive=0,
            estimated_monthly_loss=0.0,
        )

    chains, secondary_map = detect_operational_chains(insights)
    for ins in insights:
        if ins.id in secondary_map:
            ins.is_secondary = True
            ins.chain_id     = secondary_map[ins.id]
    scenarios = _collect_scenarios(insights, secondary_map, rebuild_outcomes, notif_counts)

    # Operator learning — load decisions, adapt recommendations
    cutoff_180 = datetime.utcnow() - timedelta(days=180)
    od_res = await db.execute(
        select(OperatorDecision)
        .where(
            OperatorDecision.user_id    == uid,
            OperatorDecision.created_at >= cutoff_180,
        )
        .limit(300)
    )
    operator_profile = _load_profile(od_res.scalars().all())
    _apply_adaptations(insights, operator_profile)

    # Decision weights — must run before focus so focus can use .weight
    _apply_weights(insights, notif_counts, operator_profile)

    # Fatigue + stability
    cutoff_7 = datetime.utcnow() - timedelta(days=7)
    alerts_7d_res = await db.execute(
        select(sa_func.count()).where(
            TelegramNotificationLog.user_id == uid,
            TelegramNotificationLog.sent_at >= cutoff_7,
        )
    )
    alerts_last_7d   = alerts_7d_res.scalar() or 0
    unresolved_count = sum(1 for i in insights if i.status not in ("resolved", "dismissed"))
    resolved_90d     = sum(1 for r in all_records if r.status == "resolved")
    crisis_recurrence = sum(1 for v in notif_counts.values() if v >= 2)
    age_days         = max(0, (datetime.utcnow() - current_user.created_at).days) if hasattr(current_user, "created_at") and current_user.created_at else 0

    fatigue_score    = compute_fatigue_score(
        unresolved_count=unresolved_count,
        alerts_last_7d=alerts_last_7d,
        ignored_categories=operator_profile.ignore_counts,
        focus_churn=0,  # focus churn tracking is future work
    )
    stability_credit = compute_stability_credit(
        resolved_count_90d=resolved_90d,
        crisis_recurrence_count=crisis_recurrence,
        operational_age_days=age_days,
    )

    focus = _build_focus(insights, chains, scenarios, resolved_history, notif_counts, stability_credit)

    active   = [i for i in insights if i.status not in ("resolved", "dismissed")]
    warnings = [i for i in active   if i.type == "warning"]
    positive = [i for i in active   if i.type == "positive"]

    monthly_loss = 0.0
    for ins in warnings:
        if ins.impact and ins.impact.sign == "negative":
            m = re.search(r"[\d\s]+(?=\s*₽)", ins.impact.estimate.replace("\xa0", "").replace(" ", ""))
            if m:
                try:
                    monthly_loss += float(m.group().replace(" ", ""))
                except Exception as exc:
                    logger.warning(
                        "monthly_loss_parse_failed",
                        extra={"user_id": uid, "insight_key": ins.key, "error": str(exc)},
                    )

    categories  = {_extract_category(i.key) for i in insights}
    pref_data   = await _compute_preference_scores(uid, categories, db)
    _debug      = settings.app_env != "production"

    focused = _focused_filter(insights, pref_data=pref_data, include_debug=_debug)
    # Fatigue protection: suppress background noise when operator is overwhelmed
    if fatigue_score > 0.6:
        focused = [i for i in focused if (i.weight or 0) >= 50 or i.type == "positive"]

    # Sprint 22: cross-product portfolio patterns; Sprint 28: root cause + memory enrichment
    _pp_raw = _detect_portfolio(
        [_insight_summary(i) for i in insights],
        resolved_history=resolved_history,
        insights_raw=insights,
    )
    pp_out  = [PortfolioPattern(**vars(p)) for p in _pp_raw]
    # Sprint 23: decision confidence
    _enrich_decision_confidence(insights, _pp_raw)
    # Sprint 24: signal lifecycle
    _enrich_signal_lifecycle(insights, resolved_history, notif_counts, first_seen_map)
    # Sprint 26: outcome feedback — requires lifecycle_stage already set
    _enrich_outcome_feedback(insights, notif_counts)
    # Sprint 27: signal age decay — applies penalty to confidence_score; requires lifecycle_stage
    _enrich_signal_decay(insights, notif_counts, first_seen_map, resolved_history)
    # Sprint 25: operational intelligence summary (runs last — uses fully enriched insights)
    _op_summary = _make_op_summary(insights, _pp_raw, resolved_history, fatigue_score, stability_credit)
    # Sprint 32: execution sequencing — stabilization order
    _seq_raw = _build_sequence(insights, _pp_raw, focus, fatigue_score)
    _enrich_sequencing(insights, _seq_raw)
    seq_out  = [SequencedActionOut(**_dc.asdict(s)) for s in _seq_raw]
    if _op_summary:
        _op_summary.sequencing_summary_line = _seq_summary_line(_seq_raw)
    # Sprint 33: operational trajectory — pressure direction + reversibility
    # Must run after lifecycle/decay/sequencing; adjusts weights before focus rebuild
    _enrich_trajectory(insights, _pp_raw, operator_profile)
    # Sprint 34: tradeoff intelligence — secondary consequences of intervention
    _enrich_tradeoff(insights)
    # Sprint 35: failure forecast — operational foresight layer
    _enrich_failure_forecast(insights, _pp_raw)
    # Sprint 36: recovery paths — how pressure typically resolves
    _enrich_recovery_paths(insights, _pp_raw)
    # Sprint 37: operator capacity — bandwidth and defer guidance
    _cap_dc   = _compute_capacity(insights, _pp_raw, fatigue_score, stability_credit)
    cap_out   = OperationalCapacityOut(
        capacity_state=_cap_dc.capacity_state,
        operational_bandwidth_score=_cap_dc.operational_bandwidth_score,
        overload_risk=_cap_dc.overload_risk,
        defer_categories=_cap_dc.defer_categories,
        capacity_note=_cap_dc.capacity_note,
    )
    # Capacity-aware focus pre-filter: suppress deferrable/fading signals when saturated/overloaded
    _focus_inputs = insights
    if _cap_dc.capacity_state in ("saturated", "overloaded"):
        _NEVER_DEFER_CATS = {"low_stock", "margin_crisis"}
        _focus_inputs = [
            i for i in insights
            if i.key.split(":")[0] in _NEVER_DEFER_CATS
            or getattr(i, "signal_decay_state", None) != "fading"
            or getattr(i, "signal_lifecycle_stage", None) in ("recurring",)
            or getattr(i, "reversibility_state", None) == "structurally_locked"
            or (getattr(i, "sequence_stage", None) or 0) == 1
        ]
    # Sprint 38: stabilization lock — observation window pacing
    _enrich_stabilization_lock(insights, _cap_dc.capacity_state)
    # Sprint 39: counterfactual pressure — inaction cost and timing intelligence
    _enrich_counterfactual(insights, _pp_raw)
    # Sprint 40: operator strategy profile — behavioral pattern analysis
    _strategy_dc = _compute_strategy(insights, _pp_raw, fatigue_score, stability_credit)
    strategy_out = OperatorStrategyProfileOut(
        intervention_style=_strategy_dc.intervention_style,
        pacing_discipline=_strategy_dc.pacing_discipline,
        recovery_patience=_strategy_dc.recovery_patience,
        structural_decision_tendency=_strategy_dc.structural_decision_tendency,
        operational_volatility_source=_strategy_dc.operational_volatility_source,
        strategic_stability_score=_strategy_dc.strategic_stability_score,
        stability_band=_strategy_dc.stability_band,
        coaching_note=_strategy_dc.coaching_note,
        profile_confidence=_strategy_dc.profile_confidence,
    )
    # Sprint 42: comparative simulation — two-path operational comparison per insight
    _enrich_comparisons(insights, _cap_dc.capacity_state)
    # Sprint 43: strategy commitment — operational branch tracking
    _commitment_dc  = _compute_commitment(insights, _pp_raw)
    _commitment_out = StrategyCommitmentOut(
        strategy_type=_commitment_dc.strategy_type,
        commitment_state=_commitment_dc.commitment_state,
        interruption_risk=_commitment_dc.interruption_risk,
        observability_quality=_commitment_dc.observability_quality,
        commitment_score=_commitment_dc.commitment_score,
        commitment_note=_commitment_dc.commitment_note,
        estimated_observation_window_days=_commitment_dc.estimated_observation_window_days,
    )
    _apply_commitment_weights(insights, _commitment_dc.commitment_state)
    # Sprint 44: observability recovery forecast — when signal becomes interpretable again
    _enrich_observability_recovery(insights)
    # Sprint 48: adaptive intervention timing — when to intervene
    _enrich_timing(insights, _commitment_dc.commitment_state)
    # Sprint 45: opportunity cost intelligence — economics of delayed decisions
    _enrich_opportunity_cost(insights)
    # Sprint 49: intervention reversal — diminishing returns + rollback economics
    _enrich_reversal(insights)
    # Sprint 50: secondary pressure cascade — pressure propagation into adjacent zones
    _enrich_cascade(insights)
    # Sprint 51: resilience snapshot — point-in-time operational shock absorption capacity
    _enrich_resilience(insights)
    # Sprint 52: resilience trajectory — how operational elasticity evolves over time
    _enrich_resilience_trajectory(insights)
    # Sprint 53: adaptive capacity — direction of operational adaptation over cycles
    _enrich_adaptive_capacity(insights)
    # Sprint 54: strategic memory drift — divergence from historically effective recovery doctrine
    _enrich_strategic_memory_drift(insights)
    # Sprint 55: operational regime — systemic operating mode across all signals
    _regime_dc  = _compute_regime(
        insights=insights,
        commitment_state=_commitment_dc.commitment_state,
        capacity_state=_cap_dc.capacity_state,
    )
    _apply_regime_weights(insights, _regime_dc.regime)
    _regime_out = OperationalRegimeOut(
        regime=_regime_dc.regime,
        regime_direction=_regime_dc.regime_direction,
        operational_posture=_regime_dc.operational_posture,
        resilience_context=_regime_dc.resilience_context,
        intervention_tolerance=_regime_dc.intervention_tolerance,
        observability_quality=_regime_dc.observability_quality,
        regime_note=_regime_dc.regime_note,
        regime_confidence=_regime_dc.regime_confidence,
    )
    # Sprint 56: decision energy — operational energy cost of stabilization interventions
    _energy_dc  = _compute_energy(
        insights=insights,
        capacity_state=_cap_dc.capacity_state,
        regime=_regime_dc.regime,
    )
    _apply_energy_weights(insights, _energy_dc.energy_state)
    _energy_out = DecisionEnergyOut(
        energy_state=_energy_dc.energy_state,
        coordination_load=_energy_dc.coordination_load,
        observability_load=_energy_dc.observability_load,
        stabilization_burden=_energy_dc.stabilization_burden,
        execution_complexity=_energy_dc.execution_complexity,
        energy_note=_energy_dc.energy_note,
        energy_confidence=_energy_dc.energy_confidence,
    )
    # Sprint 57: operational phase transition — systemic phase the portfolio is entering
    _phase_dc  = _compute_phase(
        insights=insights,
        regime=_regime_dc.regime,
        capacity_state=_cap_dc.capacity_state,
        energy_state=_energy_dc.energy_state,
    )
    _apply_phase_weights(insights, _phase_dc.phase)
    _phase_out = OperationalPhaseTransitionOut(
        phase=_phase_dc.phase,
        transition_direction=_phase_dc.transition_direction,
        transition_velocity=_phase_dc.transition_velocity,
        transition_stability=_phase_dc.transition_stability,
        transition_driver=_phase_dc.transition_driver,
        phase_note=_phase_dc.phase_note,
        phase_confidence=_phase_dc.phase_confidence,
    )
    # Sprint 58: stability topology — structural load distribution across execution layers
    _topo_dc  = _compute_topology(
        insights=insights,
        regime=_regime_dc.regime,
        capacity_state=_cap_dc.capacity_state,
        energy_state=_energy_dc.energy_state,
        phase=_phase_dc.phase,
    )
    _apply_topology_weights(insights, _topo_dc.topology_state)
    _topo_out = StabilityTopologyOut(
        topology_state=_topo_dc.topology_state,
        dominant_stability_layer=_topo_dc.dominant_stability_layer,
        weakest_stability_layer=_topo_dc.weakest_stability_layer,
        compensation_behavior=_topo_dc.compensation_behavior,
        structural_balance=_topo_dc.structural_balance,
        remaining_flexibility=_topo_dc.remaining_flexibility,
        topology_note=_topo_dc.topology_note,
        topology_confidence=_topo_dc.topology_confidence,
    )
    # Sprint 59: operational doctrine — behavioral institutionalization intelligence
    _doctrine_dc  = _compute_doctrine(
        insights=insights,
        regime=_regime_dc.regime,
        phase=_phase_dc.phase,
        topology_state=_topo_dc.topology_state,
        energy_state=_energy_dc.energy_state,
    )
    _apply_doctrine_weights(insights, _doctrine_dc.doctrine_state)
    _doctrine_out = OperationalDoctrineOut(
        doctrine_state=_doctrine_dc.doctrine_state,
        doctrine_pattern=_doctrine_dc.doctrine_pattern,
        adaptation_mode=_doctrine_dc.adaptation_mode,
        institutionalization_level=_doctrine_dc.institutionalization_level,
        doctrine_flexibility=_doctrine_dc.doctrine_flexibility,
        doctrine_note=_doctrine_dc.doctrine_note,
        doctrine_confidence=_doctrine_dc.doctrine_confidence,
    )
    # Sprint 60: institutional inertia — resistance to operational change
    _inertia_dc  = _compute_inertia(
        insights=insights,
        regime=_regime_dc.regime,
        phase=_phase_dc.phase,
        topology_state=_topo_dc.topology_state,
        energy_state=_energy_dc.energy_state,
        doctrine_state=_doctrine_dc.doctrine_state,
    )
    _apply_inertia_weights(insights, _inertia_dc.inertia_state)
    _inertia_out = InstitutionalInertiaOut(
        inertia_state=_inertia_dc.inertia_state,
        adaptation_resistance=_inertia_dc.adaptation_resistance,
        behavioral_repeatability=_inertia_dc.behavioral_repeatability,
        structural_elasticity=_inertia_dc.structural_elasticity,
        recovery_mobility=_inertia_dc.recovery_mobility,
        inertia_driver=_inertia_dc.inertia_driver,
        inertia_window_days=_inertia_dc.inertia_window_days,
        inertia_note=_inertia_dc.inertia_note,
        inertia_confidence=_inertia_dc.inertia_confidence,
    )
    # Sprint 61: structural recovery capacity — can the system structurally restore itself?
    _recovery_cap_dc  = _compute_recovery_cap(
        insights=insights,
        regime=_regime_dc.regime,
        phase=_phase_dc.phase,
        topology_state=_topo_dc.topology_state,
        energy_state=_energy_dc.energy_state,
        doctrine_state=_doctrine_dc.doctrine_state,
        inertia_state=_inertia_dc.inertia_state,
    )
    _apply_recovery_cap_weights(insights, _recovery_cap_dc.recovery_state)
    _recovery_cap_out = StructuralRecoveryCapacityOut(
        recovery_state=_recovery_cap_dc.recovery_state,
        structural_recoverability=_recovery_cap_dc.structural_recoverability,
        recovery_elasticity=_recovery_cap_dc.recovery_elasticity,
        restructuring_requirement=_recovery_cap_dc.restructuring_requirement,
        continuity_dependence=_recovery_cap_dc.continuity_dependence,
        structural_recovery_horizon=_recovery_cap_dc.structural_recovery_horizon,
        recovery_window_days=_recovery_cap_dc.recovery_window_days,
        structural_reversibility_index=_recovery_cap_dc.structural_reversibility_index,
        recovery_capacity_note=_recovery_cap_dc.recovery_capacity_note,
        recovery_capacity_confidence=_recovery_cap_dc.recovery_capacity_confidence,
    )
    # Sprint 47: decision drift — meta-decision intelligence / intervention coherence
    _drift_dc = _compute_drift(
        insights=insights,
        commitment_state=_commitment_dc.commitment_state,
        commitment_shift_type=_commitment_dc.strategy_shift.shift_type if _commitment_dc.strategy_shift else None,
        interruption_risk=_commitment_dc.interruption_risk,
        observability_quality=_commitment_dc.observability_quality,
        intervention_style=_strategy_dc.intervention_style,
        pacing_discipline=_strategy_dc.pacing_discipline,
    )
    _drift_out = DecisionDriftOut(
        drift_state=_drift_dc.drift_state,
        drift_note=_drift_dc.drift_note,
        intervention_overlap=_drift_dc.intervention_overlap,
        sequencing_continuity=_drift_dc.sequencing_continuity,
        observation_reset_count=_drift_dc.observation_reset_count,
    )
    # Rebuild focus with trajectory + counterfactual + commitment-adjusted weights
    focus = _build_focus(_focus_inputs, chains, scenarios, resolved_history, notif_counts, stability_credit)
    if _op_summary:
        _op_summary.trajectory_summary_line = _trajectory_summary_line(insights)

    return InsightsResponse(
        insights=insights,
        focused_insights=focused,
        operational_chains=chains,
        operational_scenarios=scenarios,
        operational_focus=focus,
        portfolio_patterns=pp_out,
        operational_summary=_op_summary,
        stabilization_sequence=seq_out,
        operational_capacity=cap_out,
        operator_strategy_profile=strategy_out,
        strategy_commitment=_commitment_out,
        decision_drift=_drift_out,
        operational_regime=_regime_out,
        decision_energy=_energy_out,
        operational_phase_transition=_phase_out,
        stability_topology=_topo_out,
        operational_doctrine=_doctrine_out,
        institutional_inertia=_inertia_out,
        structural_recovery_capacity=_recovery_cap_out,
        fatigue_score=fatigue_score,
        stability_credit=stability_credit,
        is_demo=False,
        total_active=len(active),
        has_data=True,
        total_warnings=len(warnings),
        total_positive=len(positive),
        estimated_monthly_loss=monthly_loss,
    )


@router.post("/insights/{insight_key:path}/status", response_model=UpdateStatusResponse)
async def update_insight_status(
    insight_key: str,
    body: UpdateStatusRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uid = current_user.id

    res = await db.execute(
        select(InsightRecord).where(
            InsightRecord.user_id == uid,
            InsightRecord.insight_key == insight_key,
        )
    )
    record = res.scalar_one_or_none()

    if record:
        record.status     = body.status
        record.updated_at = datetime.utcnow()
    else:
        record = InsightRecord(
            id=str(uuid.uuid4()),
            user_id=uid,
            insight_key=insight_key,
            status=body.status,
        )
        db.add(record)

    # ── Operator learning: log decision ───────────────────────────────────────
    if body.status in ("resolved", "dismissed"):
        itype    = insight_key.split(":")[0]
        accepted = body.status == "resolved"
        ignored  = body.status == "dismissed"
        resolve_days: int | None = None
        if accepted and record and record.created_at:
            resolve_days = max(0, (datetime.utcnow() - record.created_at).days)
        od = OperatorDecision(
            id=str(uuid.uuid4()),
            user_id=uid,
            insight_type=itype,
            action_taken="accepted" if accepted else "ignored",
            accepted=accepted,
            ignored=ignored,
            resolved_after_days=resolve_days,
        )
        db.add(od)

    await db.commit()
    await db.refresh(record)
    return UpdateStatusResponse(ok=True, record_id=record.id, new_status=record.status)


# ══════════════════════════════════════════════════════════════════════════════
# ME-6 — Insight Execute Layer. Turns an insight into a real marketplace action
# through the SHARED executor. Action Engine stays a DECISION layer: it builds
# the plan (insight_mapping) and delegates execution to Executor.execute().
# ══════════════════════════════════════════════════════════════════════════════
from services.marketplace import executor as _executor                 # noqa: E402
from services.marketplace import insight_mapping as _imap              # noqa: E402
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
                idempotency_key=f"review:{r.id}", dry_run=body.dry_run,
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
    res = await _executor.execute(
        db=db, user_id=uid, action_type=plan.action_type, payload=plan.payload,
        mode="manual_l3", insight_key=insight_key, decision_id=decision_id,
        dry_run=body.dry_run,
    )

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
