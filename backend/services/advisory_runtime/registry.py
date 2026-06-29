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
from .producers import run_growth_producer, run_legal_producer, run_review_producer


@dataclass(frozen=True)
class ProducerSpec:
    """One registered advisory producer. Runtime-visible surface only."""
    key: str
    run: Callable[[RuntimeContext], Awaitable[ProducerResult]]
    cadence_seconds: int
    enabled: bool


# Registered producers.
#  - growth: DISABLED — uses permissive default thresholds; must not auto-run before
#    per-user threshold sourcing is decided.
#  - legal: ENABLED (A8) — the first LIVE-SAFE advisory producer. Advisory-only,
#    binding AUTO_FORBIDDEN (can never bind an executor), threshold-free, DB-headless,
#    no marketplace read. Running it on the daily cadence creates legal_signal rows
#    only — no Decision, no Apply, no executor, no marketplace write.
ADVISORY_PRODUCERS: Tuple[ProducerSpec, ...] = (
    ProducerSpec(key="growth", run=run_growth_producer, cadence_seconds=3600, enabled=False),
    ProducerSpec(key="legal", run=run_legal_producer, cadence_seconds=86400, enabled=True),
    # review: second live-safe candidate (advisory-only; publish_review_response is
    # PAYLOAD_NOT_DERIVABLE and negatives are MANUAL_ONLY → never executable).
    # Shipped DISABLED — adapter/shadow only; enable flip is a separate PR (A10).
    ProducerSpec(key="review", run=run_review_producer, cadence_seconds=86400, enabled=False),
)
