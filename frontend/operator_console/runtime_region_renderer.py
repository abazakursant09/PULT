"""Runtime region renderer — deterministic SVG bars of accumulation regions."""
from __future__ import annotations

from .runtime_console_topology import svg_open, svg_rect, svg_text, svg_close

ROW_H = 24
BAR_X = 180
BAR_MAX = 300
WIDTH = 520


def render_regions(state) -> str:
    regions = state.application.pressure["accumulation_regions"]  # sorted dict
    items = list(regions.items())
    height = max(ROW_H * (len(items) + 1), ROW_H * 2)
    peak = max([w for _, w in items], default=1) or 1

    parts = [svg_open(WIDTH, height, "runtime regions")]
    parts.append(svg_text(10, ROW_H - 8, "runtime regions"))
    for i, (region, weight) in enumerate(items):
        y = ROW_H * (i + 1)
        bar = (weight * BAR_MAX) // peak
        parts.append(svg_text(10, y + 16, region))
        parts.append(svg_rect(BAR_X, y + 4, bar, ROW_H - 10, "region-bar"))
        parts.append(svg_text(BAR_X + bar + 6, y + 16, str(weight), "value"))
    parts.append(svg_close())
    return "".join(parts)
