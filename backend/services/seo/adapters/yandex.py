"""Yandex SEO adapter — honest stub (A3). No capabilities yet → degrades honestly.
The only place Yandex-specific SEO code may later live. No marketplace client imported."""
from __future__ import annotations

from typing import Optional

from ..adapter import SnapshotResult, SnapshotUnavailable


class YandexSeoAdapter:
    def marketplace(self) -> str:
        return "yandex"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    async def build_snapshot(self, *, listing_id: str, token: Optional[str] = None) -> SnapshotResult:
        return SnapshotUnavailable("yandex", "adapter_not_implemented",
                                   "Yandex SEO snapshot not implemented (A3 stub)")
