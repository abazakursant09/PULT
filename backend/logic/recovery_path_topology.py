"""
Recovery Path Topology — Sprint 62.
Causal transition graph model for structural recovery space.
Measures compression of recovery paths, bottleneck identification, density-modulated weighting.
NOT route planning. NOT outcome prediction. Structural accessibility diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# STATE DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

_STATE_ORDER: List[str] = [
    "structurally_recoverable",        # 0 — best
    "recoverable_with_adaptation",     # 1
    "constrained_recovery",            # 2
    "restructuring_dependent",         # 3
    "continuity_without_recovery",     # 4
    "structurally_exhausted",          # 5 — worst
]

_STATE_IDX: dict[str, int] = {s: i for i, s in enumerate(_STATE_ORDER)}

# Base accessibility: how easily can transitions happen through each state toward recovery
_STATE_ACCESSIBILITY_BASE: dict[str, float] = {
    "structurally_recoverable":     1.00,
    "recoverable_with_adaptation":  0.85,
    "constrained_recovery":         0.65,
    "restructuring_dependent":      0.40,
    "continuity_without_recovery":  0.20,
    "structurally_exhausted":       0.05,
}

# ─────────────────────────────────────────────────────────────────────────────
# PATH TYPES & WEIGHT MULTIPLIERS
# effective_weight = PATH_BASE_MULTIPLIERS[dominant_path_type] × (1 − density)
# ─────────────────────────────────────────────────────────────────────────────

PATH_BASE_MULTIPLIERS: dict[str, float] = {
    "direct_recovery_path":        1.00,
    "adaptive_recovery_path":      1.10,
    "restructuring_required_path": 1.25,
    "collapse_proximity_path":     1.40,
    "irreversible_path":           1.60,
}

# ─────────────────────────────────────────────────────────────────────────────
# GRAPH EDGES
# (from_idx, to_idx, signal_type, base_path_type)
# Recovery direction: to_idx < from_idx (toward better state)
# Degradation direction: to_idx > from_idx (toward worse state)
# ─────────────────────────────────────────────────────────────────────────────

_GRAPH_EDGES: List[Tuple[int, int, str, str]] = [
    # Recovery direction
    (5, 4, "recovery",    "restructuring_required_path"),
    (4, 3, "recovery",    "adaptive_recovery_path"),
    (3, 2, "recovery",    "adaptive_recovery_path"),
    (2, 1, "recovery",    "direct_recovery_path"),
    (1, 0, "recovery",    "direct_recovery_path"),
    # Degradation direction
    (0, 1, "degradation", "adaptive_recovery_path"),
    (1, 2, "degradation", "collapse_proximity_path"),
    (2, 3, "degradation", "collapse_proximity_path"),
    (3, 4, "rigidity",    "collapse_proximity_path"),
    (4, 5, "rigidity",    "irreversible_path"),
    # Skip-level collapse
    (2, 4, "degradation", "collapse_proximity_path"),
    (3, 5, "rigidity",    "irreversible_path"),
]

# ─────────────────────────────────────────────────────────────────────────────
# DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RecoveryPathTopology:
    recovery_path_density:        float  # MIN accessibility score along recovery path from current state
    bottleneck_state:             str    # state with minimum accessibility score
    min_accessibility_state:      str    # same as bottleneck_state (alt field for frontend footer)
    dominant_path_type:           str    # path type characterizing the topology
    direct_recovery_path_count:   int
    adaptive_recovery_path_count: int
    blocked_edges_count:          int
    irreversible_edges_count:     int
    path_topology_note:           str
    path_topology_confidence:     int


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _modulate_accessibility(
    doctrine_state: Optional[str],
    inertia_state:  Optional[str],
    recovery_state: Optional[str],
    energy_state:   Optional[str],
) -> dict[str, float]:
    scores = dict(_STATE_ACCESSIBILITY_BASE)

    # Doctrine rigidity compresses upper recovery states (best → harder to reach)
    if doctrine_state == "rigid_operational_doctrine":
        scores["structurally_recoverable"]    *= 0.70
        scores["recoverable_with_adaptation"] *= 0.78
        scores["constrained_recovery"]        *= 0.85
    elif doctrine_state == "structurally_embedded_doctrine":
        scores["structurally_recoverable"]    *= 0.80
        scores["recoverable_with_adaptation"] *= 0.87
    elif doctrine_state == "stabilization_dependency":
        scores["structurally_recoverable"]    *= 0.90

    # Inertia compresses lower recovery states (worst → harder to exit)
    if inertia_state == "institutional_freeze":
        scores["structurally_exhausted"]      *= 0.50
        scores["continuity_without_recovery"] *= 0.60
        scores["restructuring_dependent"]     *= 0.72
    elif inertia_state == "locked_operational_behavior":
        scores["structurally_exhausted"]      *= 0.65
        scores["continuity_without_recovery"] *= 0.72
        scores["restructuring_dependent"]     *= 0.82
    elif inertia_state == "structural_inertia":
        scores["continuity_without_recovery"] *= 0.82
        scores["restructuring_dependent"]     *= 0.88

    # Recovery capacity state (Sprint 61) adds context-aware pressure
    if recovery_state == "structurally_exhausted":
        scores["structurally_exhausted"]      *= 0.70
        scores["continuity_without_recovery"] *= 0.80
    elif recovery_state == "continuity_without_recovery":
        scores["continuity_without_recovery"] *= 0.80
        scores["restructuring_dependent"]     *= 0.88
    elif recovery_state == "restructuring_dependent":
        scores["restructuring_dependent"]     *= 0.88

    # Energy signals apply uniformly
    if energy_state in ("depleted", "critical", "structurally_exhausting"):
        for k in scores:
            scores[k] *= 0.90
    elif energy_state == "declining":
        for k in scores:
            scores[k] *= 0.96

    return {k: max(0.01, min(1.00, v)) for k, v in scores.items()}


def _edge_is_blocked(
    from_idx:    int,
    to_idx:      int,
    scores:      dict[str, float],
    doctrine:    Optional[str],
    inertia:     Optional[str],
) -> bool:
    """True if this recovery-direction edge is blocked by current system state."""
    target_state = _STATE_ORDER[to_idx]
    if scores[target_state] < 0.25:
        return True
    if inertia == "institutional_freeze" and from_idx >= 3:
        return True
    if inertia == "locked_operational_behavior" and from_idx >= 4:
        return True
    if doctrine == "rigid_operational_doctrine" and from_idx >= 3 and to_idx <= 1:
        return True
    return False


def _edge_is_irreversible(
    from_idx:    int,
    to_idx:      int,
    signal_type: str,
    doctrine:    Optional[str],
    inertia:     Optional[str],
) -> bool:
    """True if this degradation-direction edge is irreversible (no return path)."""
    if signal_type == "rigidity" and from_idx >= 3:
        if inertia in ("institutional_freeze", "locked_operational_behavior"):
            return True
        if doctrine in ("rigid_operational_doctrine", "structurally_embedded_doctrine"):
            return True
    if to_idx == 5 and inertia in ("institutional_freeze", "locked_operational_behavior"):
        return True
    return False


def _pick_dominant(
    direct: int, adaptive: int, blocked: int, irreversible: int, density: float
) -> str:
    if density < 0.18:
        return "irreversible_path"
    if density < 0.32:
        return "collapse_proximity_path"
    if density < 0.48:
        return "restructuring_required_path"
    if blocked > (direct + adaptive):
        return "collapse_proximity_path"
    if adaptive > direct:
        return "adaptive_recovery_path"
    return "direct_recovery_path"


def _topology_note(density: float, bottleneck: str, dominant: str) -> str:
    b = bottleneck.replace("_", " ")
    if dominant == "irreversible_path":
        return (
            f"Структурная топология перехода демонстрирует признаки необратимой компрессии через {b}. "
            "Пространство восстановительных маршрутов сужено до минимального уровня структурной доступности. "
            "Переходы через bottleneck-состояние заблокированы системными ограничениями, "
            "не устранимыми через локальную адаптацию."
        )
    if dominant == "collapse_proximity_path":
        return (
            f"Граф переходов концентрируется в зоне collapse-proximity маршрутов; bottleneck-узел — {b}. "
            "Структурное пространство восстановления существенно сужено; прямые и адаптивные переходы ограничены. "
            "Система удерживает операционный continuity без активации recovery routes."
        )
    if dominant == "restructuring_required_path":
        return (
            f"Топология восстановления доступна только через restructuring-class переходы; "
            f"bottleneck — {b} (density: {density:.2f}). "
            "Активация восстановительных путей требует системной реструктуризации, "
            "а не локальной операционной коррекции."
        )
    if dominant == "adaptive_recovery_path":
        return (
            f"Восстановительные маршруты доступны через адаптивные переходы при bottleneck в {b}. "
            f"Плотность recovery-пространства: {density:.2f}. "
            "Система сохраняет управляемый recovery bandwidth без прямых маршрутов восстановления."
        )
    return (
        f"Топология перехода сохраняет открытость структурного recovery-пространства (density: {density:.2f}). "
        f"Bottleneck в {b} находится в пределах операционной компенсации. "
        "Прямые восстановительные маршруты доступны без системного давления."
    )


def _confidence(
    insights:       list,
    doctrine_state: Optional[str],
    inertia_state:  Optional[str],
    recovery_state: Optional[str],
    density:        float,
) -> int:
    score = 72
    n_active = len([i for i in insights if not getattr(i, "is_stale", False)])
    if n_active >= 5:
        score += 8
    n_recurring = sum(
        1 for i in insights
        if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
    )
    if n_recurring >= 2:
        score += 6
    if (
        doctrine_state in ("rigid_operational_doctrine", "structurally_embedded_doctrine")
        and inertia_state in ("institutional_freeze", "locked_operational_behavior", "structural_inertia")
    ):
        score += 8  # corroborating structural lock signals
    if recovery_state in ("structurally_exhausted", "continuity_without_recovery", "restructuring_dependent"):
        score += 5  # Sprint 61 corroboration
    if n_active < 2:
        score -= 12
    if doctrine_state is None and inertia_state is None and recovery_state is None:
        score -= 8  # no structural context available
    n_stale = sum(1 for i in insights if getattr(i, "is_stale", False))
    if n_stale >= 3:
        score -= 6
    if density > 0.80:
        score -= 5  # topology not informative at high density
    return max(58, min(97, score))


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def compute_recovery_path_topology(
    insights:        list,
    regime:          Optional[str] = None,
    phase:           Optional[str] = None,
    topology_state:  Optional[str] = None,
    energy_state:    Optional[str] = None,
    doctrine_state:  Optional[str] = None,
    inertia_state:   Optional[str] = None,
    recovery_state:  Optional[str] = None,
) -> RecoveryPathTopology:
    """
    Compute structural recovery path topology.
    density = MIN accessibility score along the recovery path from current state to structurally_recoverable.
    Bottleneck = state with minimum accessibility. Edge counts are graph-global.
    """
    scores = _modulate_accessibility(doctrine_state, inertia_state, recovery_state, energy_state)

    # Recovery path: states from current position down to 0 (structurally_recoverable)
    current_idx = _STATE_IDX.get(recovery_state, 2) if recovery_state else 2
    path_states  = [_STATE_ORDER[i] for i in range(current_idx + 1)]
    path_scores  = {s: scores[s] for s in path_states}

    bottleneck_state = min(path_scores, key=lambda s: path_scores[s])
    density          = round(path_scores[bottleneck_state], 3)

    # Traverse all graph edges for global edge classification
    direct_count   = 0
    adaptive_count = 0
    blocked_count  = 0
    irrev_count    = 0

    for from_idx, to_idx, signal_type, path_type in _GRAPH_EDGES:
        is_recovery    = to_idx < from_idx
        is_degradation = to_idx > from_idx

        if is_recovery:
            if _edge_is_blocked(from_idx, to_idx, scores, doctrine_state, inertia_state):
                blocked_count += 1
            else:
                if path_type == "direct_recovery_path":
                    direct_count += 1
                elif path_type in ("adaptive_recovery_path", "restructuring_required_path"):
                    adaptive_count += 1

        if is_degradation:
            if _edge_is_irreversible(from_idx, to_idx, signal_type, doctrine_state, inertia_state):
                irrev_count += 1

    dominant = _pick_dominant(direct_count, adaptive_count, blocked_count, irrev_count, density)
    note     = _topology_note(density, bottleneck_state, dominant)
    conf     = _confidence(insights, doctrine_state, inertia_state, recovery_state, density)

    return RecoveryPathTopology(
        recovery_path_density        = density,
        bottleneck_state             = bottleneck_state,
        min_accessibility_state      = bottleneck_state,
        dominant_path_type           = dominant,
        direct_recovery_path_count   = direct_count,
        adaptive_recovery_path_count = adaptive_count,
        blocked_edges_count          = blocked_count,
        irreversible_edges_count     = irrev_count,
        path_topology_note           = note,
        path_topology_confidence     = conf,
    )
