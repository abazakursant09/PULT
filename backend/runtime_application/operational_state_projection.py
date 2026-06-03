"""Operational state projection (Post-closure directive).

Derives a deterministic, descriptive operational state from the runtime stream.
No forecasting, no scoring beyond plain accumulation, no behavioral inference —
only a structural projection of what the events already state.
"""
from __future__ import annotations

from collections import Counter, defaultdict


def category_of(entity: str) -> str:
    base = entity.split(":", 1)[0]
    return base[5:] if base.startswith("demo_") else base


def project_state(stream) -> dict:
    """Structural operational state: counts and accumulated category weight."""
    type_counts: Counter = Counter()
    category_weight: dict[str, int] = defaultdict(int)
    entities: set[str] = set()
    marketplaces: set[str] = set()

    for ev in stream.events:
        type_counts[ev["event_type"]] += 1
        entities.add(ev["entity"])
        category_weight[category_of(ev["entity"])] += ev["weight"]
        if ev["marketplace"]:
            marketplaces.add(ev["marketplace"])

    return {
        "event_count": stream.length,
        "type_counts": dict(sorted(type_counts.items())),
        "category_weight": dict(sorted(category_weight.items())),
        "entities": sorted(entities),
        "marketplaces": sorted(marketplaces),
    }
