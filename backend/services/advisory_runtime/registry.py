"""
Advisory Runtime producer registry (Phase-0 foundation) — ProducerSpec + the
declarative ADVISORY_PRODUCERS list.

A producer is registered as ONE ProducerSpec. The Runtime knows only these four
fields — `key`, `run`, `cadence_seconds`, `enabled`. It does NOT know scope,
per-listing caps, snapshot builders, thresholds, or any contour-specific detail:
those live inside the producer adapter behind `run`. Adding a producer is therefore
one registry entry, with NO change to the Runtime.

This foundation slice ships an EMPTY registry — no producer adapter exists yet, so
nothing runs. Adapters are registered in later slices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Tuple

from .runtime import RuntimeContext, ProducerResult
from .producers import run_growth_producer


@dataclass(frozen=True)
class ProducerSpec:
    """One registered advisory producer. Runtime-visible surface only."""
    key: str
    run: Callable[[RuntimeContext], Awaitable[ProducerResult]]
    cadence_seconds: int
    enabled: bool


# Registered producers. `enabled=False` everywhere in Phase-0 — NOTHING runs
# automatically (no scheduler tick, no runner are wired yet).
ADVISORY_PRODUCERS: Tuple[ProducerSpec, ...] = (
    ProducerSpec(key="growth", run=run_growth_producer, cadence_seconds=3600, enabled=False),
)
