"""Runtime dashboard renderer — composite deterministic HTML embedding every SVG
view plus the replay identity and runtime_application_hash. Read-only."""
from __future__ import annotations

from .runtime_console_topology import build_console_topology, svg_escape
from .runtime_region_renderer import render_regions
from .pressure_visualization_renderer import render_pressure
from .drift_visualization_renderer import render_drift
from .intervention_surface_renderer import render_interventions
from .replay_timeline_renderer import render_replay

_CSS = (
    "body{font-family:monospace;background:#0b0b0d;color:#d8d8df;margin:0;padding:16px}"
    "h1{font-size:16px}section{margin:14px 0}h2{font-size:13px;color:#8a8aa0}"
    "text{font:11px monospace;fill:#c8c8d2}.value,.marker,.cell{fill:#9aa0ff}"
    ".region-bar,.pressure-bar{fill:#3a3a6a}.pressure-dissipating{fill:#2f6a4a}"
    ".drift-cell{fill:#6a4a2f}.drift-unstable{fill:#8a2f2f}"
    ".interv-surface{fill:#23233a;stroke:#4a4a7a}.interv-dissipating{fill:#1f3a2a;stroke:#2f6a4a}"
    ".replay-cell{fill:#23233a;stroke:#4a4a7a}.window-bracket{stroke:#9aa0ff;stroke-width:2}"
    ".meta{color:#6a6a80;font-size:11px}"
)


def render_dashboard(state) -> str:
    topo = build_console_topology(state)
    head = (
        f'<h1>PULT Operator Console — read-only</h1>'
        f'<div class="meta">descriptive_only={topo["descriptive_only"]} '
        f'execution_authority={topo["execution_authority"]} '
        f'mutation_authority={topo["mutation_authority"]}</div>'
        f'<div class="meta">runtime_application_hash {svg_escape(topo["runtime_application_hash"])}</div>'
        f'<div class="meta">operator_console_hash {svg_escape(topo["operator_console_hash"])}</div>'
        f'<div class="meta">events {topo["runtime_event_count"]} · '
        f'replay windows {topo["replay_identity"]["window_count"]}</div>'
    )
    sections = (
        f'<section><h2>runtime regions</h2>{render_regions(state)}</section>'
        f'<section><h2>pressure accumulation</h2>{render_pressure(state)}</section>'
        f'<section><h2>drift surface</h2>{render_drift(state)}</section>'
        f'<section><h2>intervention surfaces</h2>{render_interventions(state)}</section>'
        f'<section><h2>replay timeline</h2>{render_replay(state)}</section>'
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<title>PULT Operator Console</title><style>{_CSS}</style></head>'
        f'<body>{head}{sections}</body></html>'
    )
