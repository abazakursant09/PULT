"""Ozon SEO adapter — honest stub (A3). No capabilities yet → degrades honestly.
The only place Ozon-specific SEO code may later live. No marketplace client imported."""
from __future__ import annotations

from typing import Optional

from ..adapter import SnapshotResult, SnapshotUnavailable


class OzonSeoAdapter:
    def marketplace(self) -> str:
        return "ozon"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    async def build_snapshot(self, *, listing_id: str, token: Optional[str] = None) -> SnapshotResult:
        return SnapshotUnavailable("ozon", "adapter_not_implemented",
                                   "Ozon SEO snapshot not implemented (A3 stub)")
