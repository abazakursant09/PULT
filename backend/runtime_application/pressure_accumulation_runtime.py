"""Runtime pressure accumulation (Post-closure directive).

Describes where operational pressure accumulates (regions) and where it is being
relieved (dissipation surfaces), purely from the event stream. Descriptive only:
no forecasting, no correction, no scoring beyond accumulation of stated weights.
"""
from __future__ import annotations

from collections import defaultdict

from .operational_state_projection import category_of

# Event types that describe relief of pressure (operator-stated, not inferred).
DISSIPATION_TYPES = frozenset({
    "insight_resolved", "recovery", "dissipation", "stabilized",
})


def accumulation_regions(stream) -> dict:
    """Region -> accumulated stated weight (sorted, deterministic)."""
    region: dict[str, int] = defaultdict(int)
    for ev in stream.events:
        region[category_of(ev["entity"])] += ev["weight"]
    return dict(sorted(region.items()))


def dissipation_surfaces(stream) -> list:
    """Regions that carry at least one dissipation-type event."""
    surfaces: set[str] = set()
    for ev in stream.events:
        if ev["event_type"] in DISSIPATION_TYPES:
            surfaces.add(category_of(ev["entity"]))
    return sorted(surfaces)


def pressure_map(stream) -> dict:
    return {
        "accumulation_regions": accumulation_regions(stream),
        "dissipation_surfaces": dissipation_surfaces(stream),
    }
