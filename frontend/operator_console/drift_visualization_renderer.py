"""Drift visualization renderer — drift regions + instability markers.
Deterministic SVG. Observation only."""
from __future__ import annotations

from .runtime_console_topology import svg_open, svg_rect, svg_text, svg_close

ROW_H = 24
BAR_X = 180
BAR_W = 28
WIDTH = 520


def render_drift(state) -> str:
    drift = state.application.drift
    regions = list(drift["drift_regions"].items())
    unstable = set(drift["instability_markers"])
    height = max(ROW_H * (len(regions) + 1), ROW_H * 2)

    parts = [svg_open(WIDTH, height, "drift surface")]
    parts.append(svg_text(10, ROW_H - 8, "drift surface"))
    for i, (region, count) in enumerate(regions):
        y = ROW_H * (i + 1)
        parts.append(svg_text(10, y + 16, region))
        for j in range(count):
            cls = "drift-unstable" if region in unstable else "drift-cell"
            parts.append(svg_rect(BAR_X + j * (BAR_W + 4), y + 4, BAR_W, ROW_H - 10, cls))
        if region in unstable:
            parts.append(svg_text(BAR_X + count * (BAR_W + 4) + 6, y + 16, "instability", "marker"))
    parts.append(svg_close())
    return "".join(parts)
