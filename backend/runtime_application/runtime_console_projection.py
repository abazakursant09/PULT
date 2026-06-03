"""Operator-facing runtime view (Post-closure directive).

Aggregates the descriptive projections into a single deterministic, replay-
reconstructable console view. Descriptive only — it renders state, it does not
act on it.
"""
from __future__ import annotations

from .operational_state_projection import project_state
from .pressure_accumulation_runtime import pressure_map
from .intervention_surface_runtime import intervention_surfaces
from .drift_visualization_runtime import drift_map
from .replay_runtime_window import reconstruct
from .runtime_application_boundary import DESCRIPTIVE_ONLY


def console_view(stream, window_size: int = 3) -> dict:
    """Single deterministic operator-facing projection of the runtime."""
    return {
        "descriptive_only": DESCRIPTIVE_ONLY,
        "state": project_state(stream),
        "pressure": pressure_map(stream),
        "interventions": intervention_surfaces(stream),
        "drift": drift_map(stream),
        "replay": reconstruct(stream, window_size),
    }
