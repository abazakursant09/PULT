"""WB SEO adapter (A8) — first real path. Builds a CardSnapshot from EXISTING
internal PULT data (ProductListing + PhysicalProduct), no external API. Whatever
PULT does not store is honestly marked unavailable → rules return not_evaluated.
No WB-specific logic and no marketplace client — it reuses the generic internal
source. WB-specific API reads are a later sprint."""
from __future__ import annotations

from typing import Optional

from ..adapter import SnapshotResult, CAP_BUILD_SNAPSHOT, CAP_CARD_AUDIT
from ..internal_source import build_snapshot_from_internal


class WBSeoAdapter:
    def marketplace(self) -> str:
        return "wildberries"

    def capabilities(self) -> frozenset[str]:
        return frozenset({CAP_BUILD_SNAPSHOT, CAP_CARD_AUDIT})

    async def build_snapshot(self, *, listing_id: str, db=None,
                             token: Optional[str] = None) -> SnapshotResult:
        return await build_snapshot_from_internal(db, listing_id=listing_id,
                                                  marketplace="wildberries")
