"""Runtime intervention surfaces (Post-closure directive).

Exposes operator-visible regions where intervention is possible. OBSERVATION
ONLY — this layer never executes, schedules, or recommends an intervention. It
marks where the operator MAY look, sorted deterministically.
"""
from __future__ import annotations

from .pressure_accumulation_runtime import accumulation_regions, DISSIPATION_TYPES
from .operational_state_projection import category_of

# Descriptive visibility threshold (fixed; not tuned, not learned).
INTERVENTION_VISIBILITY_THRESHOLD = 10


def intervention_surfaces(stream) -> list:
    """Regions whose accumulated weight crosses the visibility threshold and are
    not already dissipating. Each surface is descriptive, observation-only."""
    regions = accumulation_regions(stream)
    dissipating = {
        category_of(ev["entity"]) for ev in stream.events
        if ev["event_type"] in DISSIPATION_TYPES
    }
    surfaces = []
    for region, weight in regions.items():
        if weight >= INTERVENTION_VISIBILITY_THRESHOLD:
            surfaces.append({
                "region": region,
                "accumulated_weight": weight,
                "dissipating": region in dissipating,
                "observation_only": True,
            })
    return sorted(surfaces, key=lambda s: (-s["accumulated_weight"], s["region"]))
