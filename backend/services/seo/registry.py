"""
SeoAdapter registry (SEO A3) — agnostic dispatch over the three supported
marketplaces. The engine resolves an adapter by canonical marketplace name and
talks only to the SeoAdapter contract; it never branches on a specific MP.
"""
from __future__ import annotations

from typing import Optional

from .adapter import SeoAdapter
from .adapters.wb import WBSeoAdapter
from .adapters.ozon import OzonSeoAdapter
from .adapters.yandex import YandexSeoAdapter

_ALIASES = {
    "wb": "wildberries", "wildberries": "wildberries",
    "ozon": "ozon",
    "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex",
}

_REGISTRY: dict[str, SeoAdapter] = {
    "wildberries": WBSeoAdapter(),
    "ozon": OzonSeoAdapter(),
    "yandex": YandexSeoAdapter(),
}


def _canon_mp(marketplace: Optional[str]) -> str:
    return _ALIASES.get((marketplace or "").strip().lower(), (marketplace or "").strip().lower())


def get_seo_adapter(marketplace: Optional[str]) -> Optional[SeoAdapter]:
    """Adapter for a marketplace, or None if unknown. No fallback fabrication."""
    return _REGISTRY.get(_canon_mp(marketplace))


def registered_marketplaces() -> frozenset[str]:
    return frozenset(_REGISTRY.keys())
