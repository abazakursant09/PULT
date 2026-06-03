"""Pressure visualization renderer — accumulation regions + dissipation surfaces.
Deterministic SVG. Descriptive only."""
from __future__ import annotations

from .runtime_console_topology import svg_open, svg_rect, svg_text, svg_close

ROW_H = 24
BAR_X = 180
BAR_MAX = 300
WIDTH = 560


def render_pressure(state) -> str:
    pressure = state.application.pressure
    regions = list(pressure["accumulation_regions"].items())
    dissipating = set(pressure["dissipation_surfaces"])
    height = max(ROW_H * (len(regions) + 1), ROW_H * 2)
    peak = max([w for _, w in regions], default=1) or 1

    parts = [svg_open(WIDTH, height, "pressure accumulation")]
    parts.append(svg_text(10, ROW_H - 8, "pressure accumulation"))
    for i, (region, weight) in enumerate(regions):
        y = ROW_H * (i + 1)
        bar = (weight * BAR_MAX) // peak
        cls = "pressure-dissipating" if region in dissipating else "pressure-bar"
        parts.append(svg_text(10, y + 16, region))
        parts.append(svg_rect(BAR_X, y + 4, bar, ROW_H - 10, cls))
        marker = f"{weight} (dissipating)" if region in dissipating else str(weight)
        parts.append(svg_text(BAR_X + bar + 6, y + 16, marker, "value"))
    parts.append(svg_close())
    return "".join(parts)
