"""
Category-schema Reference adapters (Phase C2c) — read-only, marketplace-agnostic normalization.

An adapter is the only place that knows a marketplace's category-schema API shape. It exposes a
tiny read-only contract and normalizes the marketplace's raw response into two canonical shapes:

  category:  {"category_id", "parent_id", "name", "path"}
  attribute: {"attribute_id", "name", "type", "is_required", "is_filterable", "is_variant",
              "max_length", "allowed_values"}   # allowed_values: list[str]

DISJOINT from the marketplace EXECUTION clients (set_price/publish_*). These adapters only READ
category reference data. The concrete adapters wrap a `client` object that performs the actual
read-only HTTP calls (WB Content API, Ozon description-category, Yandex parameters); tests inject a
mock client, so no live HTTP runs here.

MegaMarket has no category-schema API → no adapter (honest skip at the ingestion layer).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

# Marketplaces whose category schema is sourceable via API. MegaMarket is deliberately absent.
SUPPORTED_MARKETPLACES = frozenset({"wildberries", "ozon", "yandex"})


@runtime_checkable
class CategorySchemaAdapter(Protocol):
    """Read-only category-schema source for one marketplace. Normalizes to canonical shapes."""

    def marketplace(self) -> str: ...

    async def fetch_category_tree(self) -> list[dict]:
        """Canonical category dicts (category_id/parent_id/name/path)."""
        ...

    async def fetch_category_attributes(self, category_id: str) -> list[dict]:
        """Canonical attribute dicts for one category."""
        ...


def _norm_category(raw: dict) -> dict:
    return {
        "category_id": str(raw.get("category_id") or raw.get("id") or raw.get("subjectID") or ""),
        "parent_id": (str(raw["parent_id"]) if raw.get("parent_id") is not None
                      else (str(raw["parentID"]) if raw.get("parentID") is not None else None)),
        "name": raw.get("name") or raw.get("subjectName") or raw.get("categoryName") or "",
        "path": raw.get("path"),
    }


def _norm_attribute(raw: dict) -> dict:
    return {
        "attribute_id": str(raw.get("attribute_id") or raw.get("id") or raw.get("charcID") or ""),
        "name": raw.get("name") or raw.get("charcName") or "",
        "type": raw.get("type") or raw.get("charcType"),
        "is_required": bool(raw.get("is_required") or raw.get("required") or raw.get("isRequired")),
        "is_filterable": bool(raw.get("is_filterable") or raw.get("filtering") or raw.get("isFilterable")),
        "is_variant": bool(raw.get("is_variant") or raw.get("distinctive") or raw.get("isVariant")),
        "max_length": raw.get("max_length") or (raw.get("constraints") or {}).get("maxLength"),
        "allowed_values": list(raw.get("allowed_values") or raw.get("values") or []),
    }


class _ClientAdapter:
    """Thin adapter over a read-only marketplace client. The client returns raw category/attribute
    lists (marketplace-specific); this normalizes them. No HTTP here — the client does the I/O."""

    def __init__(self, marketplace: str, client):
        self._marketplace = marketplace
        self._client = client

    def marketplace(self) -> str:
        return self._marketplace

    async def fetch_category_tree(self) -> list[dict]:
        raw = await self._client.fetch_category_tree()
        return [_norm_category(r) for r in (raw or [])]

    async def fetch_category_attributes(self, category_id: str) -> list[dict]:
        raw = await self._client.fetch_category_attributes(category_id)
        return [_norm_attribute(r) for r in (raw or [])]


def get_category_schema_adapter(
    marketplace: str, client=None
) -> Optional[CategorySchemaAdapter]:
    """Return a read-only category-schema adapter for the marketplace, or None if the marketplace
    has no category-schema API (MegaMarket) — an honest skip, never a fabricated adapter."""
    if marketplace not in SUPPORTED_MARKETPLACES:
        return None                                  # e.g. megamarket → honest skip
    if client is None:
        return None                                  # no read client wired → nothing to fetch
    return _ClientAdapter(marketplace, client)
