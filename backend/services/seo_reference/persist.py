"""
Category-schema Reference persist (Phase C2c) — writes GLOBAL versioned Reference rows.

Writes normalized category + attribute dicts into marketplace_category_rows /
marketplace_category_attribute_rows. Reference Data semantics: GLOBAL (no user_id), each ingestion
run stamps a new `version` + `captured_at` + `source="api_snapshot"`; prior versions are left
untouched (immutable). Latest-wins is a READER concern (order by captured_at), not enforced here —
this layer only appends the new version.

Flush-only style consistent with the other ingestion persist modules; the caller owns the commit.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow

SOURCE = "api_snapshot"


def _version_for(captured_at: datetime) -> str:
    """Deterministic version tag from the capture instant (ISO seconds)."""
    return captured_at.strftime("%Y-%m-%dT%H:%M:%S")


async def persist_category_schema(
    db: AsyncSession, *, marketplace: str, categories: list[dict], attributes: list[dict],
    captured_at: datetime, version: Optional[str] = None,
) -> tuple[int, int]:
    """Append a new GLOBAL versioned snapshot of the category tree + attribute schema. Returns
    (categories_written, attributes_written). Prior versions remain immutable."""
    ver = version or _version_for(captured_at)

    cat_rows = []
    for c in categories:
        cid = str(c.get("category_id") or "")
        if not cid:
            continue
        cat_rows.append(MarketplaceCategoryRow(
            marketplace=marketplace, category_id=cid, parent_id=c.get("parent_id"),
            name=c.get("name") or "", path=c.get("path"),
            captured_at=captured_at, version=ver, source=SOURCE))

    attr_rows = []
    for a in attributes:
        cid = str(a.get("category_id") or "")
        aid = str(a.get("attribute_id") or "")
        if not cid or not aid:
            continue
        allowed = a.get("allowed_values")
        attr_rows.append(MarketplaceCategoryAttributeRow(
            marketplace=marketplace, category_id=cid, attribute_id=aid,
            name=a.get("name") or "", type=a.get("type"),
            is_required=bool(a.get("is_required")), is_filterable=bool(a.get("is_filterable")),
            is_variant=bool(a.get("is_variant")), max_length=a.get("max_length"),
            allowed_values_json=(json.dumps(list(allowed), ensure_ascii=False) if allowed else None),
            captured_at=captured_at, version=ver, source=SOURCE))

    db.add_all(cat_rows)
    db.add_all(attr_rows)
    await db.flush()
    return len(cat_rows), len(attr_rows)
