"""Replay timeline renderer — append-only ordinal timeline + replay windows.
Deterministic SVG (ordinal-based coordinates; no clocks, no random layout)."""
from __future__ import annotations

from .runtime_console_topology import svg_open, svg_rect, svg_text, svg_line, svg_close

CELL_W = 70
CELL_H = 30
TOP = 40
WIDTH_PAD = 20


def render_replay(state) -> str:
    replay = state.application.replay
    timeline = replay["timeline"]
    windows = replay["windows"]
    width = max(CELL_W * len(timeline) + WIDTH_PAD, 200)
    height = TOP + CELL_H + 50

    parts = [svg_open(width, height, "replay timeline")]
    parts.append(svg_text(10, 20, f'replay identity {state.application.runtime_application_hash[:16]}'))
    for ev in timeline:
        x = WIDTH_PAD + ev["ordinal"] * CELL_W
        parts.append(svg_rect(x, TOP, CELL_W - 8, CELL_H, "replay-cell"))
        parts.append(svg_text(x + 4, TOP + 19, f'#{ev["ordinal"]} {ev["event_type"][:7]}', "cell"))
    # window brackets below the cells
    for w in windows:
        x1 = WIDTH_PAD + w["start_ordinal"] * CELL_W
        x2 = WIDTH_PAD + w["end_ordinal"] * CELL_W + (CELL_W - 8)
        yb = TOP + CELL_H + 14
        parts.append(svg_line(x1, yb, x2, yb, "window-bracket"))
    parts.append(svg_close())
    return "".join(parts)
