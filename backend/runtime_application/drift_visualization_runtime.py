"""Runtime drift observation (Post-closure directive).

Describes drift activity per region and marks topology instability — where
recurring/cascading events concentrate. Visibility only: no forecasting, no
correction, no adjustment. Just what the stream already shows.
"""
from __future__ import annotations

from collections import defaultdict

from .operational_state_projection import category_of

# Event types that describe drift activity (operator-stated).
DRIFT_TYPES = frozenset({"recurrence", "drift", "cascade", "intervention_failed", "reversal"})

# A region with this many drift events is marked unstable (fixed marker, not tuned).
INSTABILITY_MARKER_THRESHOLD = 2


def drift_regions(stream) -> dict:
    """Region -> count of drift-type events (sorted, deterministic)."""
    counts: dict[str, int] = defaultdict(int)
    for ev in stream.events:
        if ev["event_type"] in DRIFT_TYPES:
            counts[category_of(ev["entity"])] += 1
    return dict(sorted(counts.items()))


def instability_markers(stream) -> list:
    """Regions whose drift activity crosses the instability marker threshold."""
    return sorted(
        region for region, n in drift_regions(stream).items()
        if n >= INSTABILITY_MARKER_THRESHOLD
    )


def drift_map(stream) -> dict:
    return {
        "drift_regions": drift_regions(stream),
        "instability_markers": instability_markers(stream),
    }
