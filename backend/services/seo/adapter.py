"""
SeoAdapter contract (SEO A3) — the single boundary between the agnostic SEO core
and any marketplace specifics.

A3 scope deliberately covers ONLY snapshot building + capability declaration.
read_metric / apply_action / content.write / search_visibility / measurement are
NOT part of this contract yet (later sprints) — those capability strings are not
even defined here, so nothing can accidentally depend on them.

Honest degradation: build_snapshot returns either a CardSnapshot or a
SnapshotUnavailable (with a reason). Never fake data, never a partial lie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Union, runtime_checkable

from .card_snapshot import CardSnapshot

# Canonical SEO capability keys available in A3. Metric/write capabilities are
# intentionally absent — they belong to later sprints.
CAP_BUILD_SNAPSHOT = "seo.build_snapshot"
CAP_CARD_AUDIT = "seo.read.card_audit"


@dataclass(frozen=True)
class SnapshotUnavailable:
    """Honest negative result: the adapter cannot produce a snapshot. No fake data."""
    marketplace: str
    reason: str                      # adapter_not_implemented | card_not_found | capability_missing | ...
    detail: Optional[str] = None


SnapshotResult = Union[CardSnapshot, SnapshotUnavailable]


@runtime_checkable
class SeoAdapter(Protocol):
    """What every marketplace SEO adapter must provide in A3."""

    def marketplace(self) -> str:
        """Canonical marketplace name (wildberries | ozon | yandex)."""
        ...

    def capabilities(self) -> frozenset[str]:
        """SEO capabilities this adapter currently supports. Empty = degrades honestly."""
        ...

    async def build_snapshot(self, *, listing_id: str, token: Optional[str] = None) -> SnapshotResult:
        """Build the canonical CardSnapshot, or return SnapshotUnavailable. Never fakes."""
        ...
