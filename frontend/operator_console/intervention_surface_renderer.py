"""Intervention surface renderer — operator-visible regions, observation only.
Deterministic SVG. No execution affordance is rendered."""
from __future__ import annotations

from .runtime_console_topology import svg_open, svg_rect, svg_text, svg_close

ROW_H = 28
WIDTH = 560


def render_interventions(state) -> str:
    surfaces = state.application.interventions  # already sorted, observation_only
    height = max(ROW_H * (len(surfaces) + 1), ROW_H * 2)

    parts = [svg_open(WIDTH, height, "intervention surfaces")]
    parts.append(svg_text(10, ROW_H - 10, "intervention surfaces (observation only)"))
    for i, surf in enumerate(surfaces):
        y = ROW_H * (i + 1)
        cls = "interv-dissipating" if surf["dissipating"] else "interv-surface"
        parts.append(svg_rect(10, y + 2, WIDTH - 20, ROW_H - 8, cls))
        label = f'{surf["region"]} — weight {surf["accumulated_weight"]}'
        if surf["dissipating"]:
            label += " (dissipating)"
        parts.append(svg_text(18, y + 18, label))
    parts.append(svg_close())
    return "".join(parts)
