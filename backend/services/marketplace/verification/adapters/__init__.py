"""Adapter registry. A dict, deliberately — never an if/elif on the marketplace.

A conditional would put marketplace knowledge back into the spine, one branch at a time,
which is exactly how a shared framework quietly becomes a Wildberries framework with a
slot for everyone else. Looking the adapter up by key keeps that knowledge inside the
adapter module where it belongs.

A marketplace with no adapter is not an error: `get_adapter` returns None and the runner
records the honest `verification_unsupported` outcome. That is how Yandex behaves today —
it needs no special case, only an absent entry.
"""
from __future__ import annotations

from typing import Optional

from .base import ProbeAdapter, ProbeContext, ProbeRequest, ProbeResponse
from .ozon import OzonProbeAdapter
from .wildberries import WildberriesProbeAdapter
from .yandex import YandexProbeAdapter

# marketplace label (as stored on MarketplaceConnection.marketplace) -> adapter
ADAPTERS: dict[str, ProbeAdapter] = {
    "wildberries": WildberriesProbeAdapter(),
    "ozon": OzonProbeAdapter(),
    "yandex": YandexProbeAdapter(),
}


def get_adapter(marketplace: str) -> Optional[ProbeAdapter]:
    """The probe for this marketplace, or None when none exists yet."""
    return ADAPTERS.get((marketplace or "").lower())


__all__ = [
    "ADAPTERS", "get_adapter",
    "ProbeAdapter", "ProbeContext", "ProbeRequest", "ProbeResponse",
]
