"""Replay-safe runtime windows (Post-closure directive).

Reconstructs the append-only timeline as deterministic windows over event
ordinals. Windowing is purely positional (ordinal-based) — no clocks, no
wall-time. Same stream + same window size always reconstruct identically.
"""
from __future__ import annotations

DEFAULT_WINDOW_SIZE = 3


def timeline(stream) -> list:
    """Ordered, append-only timeline of event summaries (by ordinal)."""
    return [
        {"ordinal": ev["ordinal"], "event_type": ev["event_type"],
         "entity": ev["entity"], "weight": ev["weight"]}
        for ev in stream.events
    ]


def windows(stream, size: int = DEFAULT_WINDOW_SIZE) -> list:
    """Deterministic, non-overlapping, ordinal-aligned replay windows."""
    if size < 1:
        size = 1
    tl = timeline(stream)
    out = []
    for start in range(0, len(tl), size):
        chunk = tl[start:start + size]
        out.append({
            "start_ordinal": chunk[0]["ordinal"],
            "end_ordinal": chunk[-1]["ordinal"],
            "ordinals": [e["ordinal"] for e in chunk],
        })
    return out


def reconstruct(stream, size: int = DEFAULT_WINDOW_SIZE) -> dict:
    return {
        "event_count": stream.length,
        "timeline": timeline(stream),
        "windows": windows(stream, size),
        "window_size": size,
    }
