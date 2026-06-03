"""Operator Console (product layer) — read-only view over Runtime Application v1.

STRICTLY a read-only consumer of backend/runtime_application/runtime_application_topology.
Renders deterministic SVG + JSON. No execution authority, no mutation paths, no
recommendations, no forecasting. Real substrate only — depends on nothing that
does not exist.
"""
from __future__ import annotations

from .runtime_console_state import ConsoleState, default_state, load_state, DEFAULT_EVENT_LOG
from .runtime_console_topology import build_console_topology, OPERATOR_CONSOLE_DOMAIN
from .runtime_dashboard_renderer import render_dashboard
from .pressure_visualization_renderer import render_pressure
from .drift_visualization_renderer import render_drift
from .intervention_surface_renderer import render_interventions
from .replay_timeline_renderer import render_replay
from .runtime_region_renderer import render_regions

__all__ = [
    "ConsoleState", "default_state", "load_state", "DEFAULT_EVENT_LOG",
    "build_console_topology", "OPERATOR_CONSOLE_DOMAIN",
    "render_dashboard", "render_pressure", "render_drift",
    "render_interventions", "render_replay", "render_regions",
]
