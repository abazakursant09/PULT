"""
Category-schema Reference ingestion runner (Phase C2c) — read-only, outside Advisory Runtime.

Orchestrates: fetch category tree → fetch attributes per category → normalize → persist GLOBAL
versioned rows. NOT a producer, NOT in the Advisory Runtime registry, NOT scheduled by the advisory
loop — this is an ingestion sibling of csv_import that a platform scheduler (or manual trigger)
runs on a slow cadence. Read-only API use (Reference role), disjoint from execution. MegaMarket /
unsupported marketplaces are honestly skipped with a reason.

The runner takes an already-built adapter (or None), so callers/tests inject a mock adapter and no
live HTTP is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .adapters import CategorySchemaAdapter, get_category_schema_adapter, SUPPORTED_MARKETPLACES
from .persist import persist_category_schema, _version_for


@dataclass(frozen=True)
class IngestionSummary:
    marketplace: str
    version: Optional[str]
    captured_at: Optional[datetime]
    categories_written: int
    attributes_written: int
    skipped_reason: Optional[str] = None


async def run_category_schema_ingestion(
    db: AsyncSession, marketplace: str, *, adapter: Optional[CategorySchemaAdapter] = None,
    client=None, captured_at: Optional[datetime] = None,
) -> IngestionSummary:
    """Ingest one marketplace's category schema into GLOBAL versioned Reference rows.

    Honest skip when the marketplace has no category-schema API (MegaMarket) or no adapter/client is
    available. Read-only: never writes to the marketplace, never called at diagnosis time."""
    ts = captured_at or datetime.utcnow()

    if marketplace not in SUPPORTED_MARKETPLACES:
        return IngestionSummary(marketplace, None, None, 0, 0,
                                skipped_reason="no_category_schema_api")

    adp = adapter or get_category_schema_adapter(marketplace, client=client)
    if adp is None:
        return IngestionSummary(marketplace, None, None, 0, 0,
                                skipped_reason="no_adapter")

    version = _version_for(ts)

    categories = await adp.fetch_category_tree()
    attributes: list[dict] = []
    for cat in categories:
        cid = str(cat.get("category_id") or "")
        if not cid:
            continue
        for attr in await adp.fetch_category_attributes(cid):
            attr = dict(attr)
            attr["category_id"] = cid                 # tag each attribute with its category
            attributes.append(attr)

    cats_written, attrs_written = await persist_category_schema(
        db, marketplace=marketplace, categories=categories, attributes=attributes,
        captured_at=ts, version=version)

    return IngestionSummary(
        marketplace=marketplace, version=version, captured_at=ts,
        categories_written=cats_written, attributes_written=attrs_written, skipped_reason=None)
