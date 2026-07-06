"""
SEO Reference merge (Phase C2d) — merge GLOBAL Reference Data into the CardSnapshot at build time.

Per the Reference Data Doctrine, Reference Data (marketplace_category_rows /
marketplace_category_attribute_rows — global, versioned, no user_id) is merged into the
diagnosis input at BUILD time, so Diagnosis stays source-agnostic. This module reads the LATEST
reference version for a resolved (marketplace, category_id) and returns the CardSnapshot pieces:
category_schema (required / filterable / variant attribute names), expected_category_path,
constraints (SeoConstraints), the variant attribute keys, and the reference version pin.

Honesty rules:
- No marketplace API here — DB only (Reference was pulled by the C2c ingestion job).
- Category resolution is DETERMINISTIC (exact id / name / path); no fuzzy matching. Unresolved →
  return None → schema/constraints stay unavailable → constraint rules stay not_evaluated.
- STALENESS GUARD: a reference version older than REFERENCE_STALENESS_DAYS is treated as absent —
  we do not diagnose on stale marketplace rules.
- Only the LATEST reference version is used; versions are never mixed.

SeoConstraints policy-vs-marketplace SPLIT (all six required, none invented per-audit):
- title_max_len ........... MARKETPLACE limit — a documented, stable per-marketplace hard limit.
- title_min_len ........... PULT audit policy (marketplaces publish no title minimum).
- description_min_len ..... PULT audit policy (a content-quality standard).
- media_min_images ........ PULT audit policy (a recommended-media standard).
- attribute_fill_rate_threshold ...... PULT audit policy.
- content_completeness_threshold ..... PULT audit policy.
These PULT policy constants are a FIXED audit standard, not per-marketplace invention.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow

from .card_snapshot import CategorySchema, SeoConstraints

# ── deterministic constants ──────────────────────────────────────────────────
REFERENCE_STALENESS_DAYS = 30

# MARKETPLACE-sourced hard limits (documented, stable). Absent marketplace → no constraints built.
MARKETPLACE_TITLE_MAX_LEN = {
    "wildberries": 60,
    "ozon": 200,
    "yandex": 150,
}

# PULT audit-policy constants — a FIXED PULT standard, NOT per-marketplace marketplace data.
PULT_TITLE_MIN_LEN = 10
PULT_DESCRIPTION_MIN_LEN = 100
PULT_MEDIA_MIN_IMAGES = 3
PULT_ATTRIBUTE_FILL_RATE_THRESHOLD = 0.7
PULT_CONTENT_COMPLETENESS_THRESHOLD = 0.75


@dataclass(frozen=True)
class ReferenceMerge:
    category_schema: CategorySchema
    expected_category_path: Optional[tuple]
    constraints: Optional[SeoConstraints]
    variant_attribute_keys: tuple          # variant attribute NAMES this category defines
    reference_version: str


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


async def _latest_category(
    db: AsyncSession, marketplace: str, card_category: Optional[str]
) -> Optional[MarketplaceCategoryRow]:
    """Deterministically resolve the card's category string to the LATEST category row.
    Match order: exact category_id, exact name, exact path (case-insensitive). No fuzzy match."""
    if not (card_category or "").strip():
        return None
    rows = (await db.execute(
        select(MarketplaceCategoryRow).where(MarketplaceCategoryRow.marketplace == marketplace)
        .order_by(MarketplaceCategoryRow.captured_at.desc()))).scalars().all()
    if not rows:
        return None
    cc = _norm(card_category)
    # exact category_id
    for r in rows:
        if _norm(r.category_id) == cc:
            return r
    # exact name
    for r in rows:
        if _norm(r.name) == cc:
            return r
    # exact path
    for r in rows:
        if _norm(r.path) == cc:
            return r
    return None


async def _latest_attributes(
    db: AsyncSession, marketplace: str, category_id: str
) -> tuple[list[MarketplaceCategoryAttributeRow], Optional[str], Optional[datetime]]:
    """Attributes of the LATEST version for (marketplace, category_id). Never mixes versions —
    picks the newest captured_at and returns only that version's rows."""
    rows = (await db.execute(
        select(MarketplaceCategoryAttributeRow).where(
            MarketplaceCategoryAttributeRow.marketplace == marketplace,
            MarketplaceCategoryAttributeRow.category_id == category_id)
        .order_by(MarketplaceCategoryAttributeRow.captured_at.desc()))).scalars().all()
    if not rows:
        return [], None, None
    latest_captured = rows[0].captured_at
    latest_version = rows[0].version
    same_version = [r for r in rows if r.captured_at == latest_captured]
    return same_version, latest_version, latest_captured


async def build_reference_merge(
    db: AsyncSession, *, marketplace: str, card_category: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[ReferenceMerge]:
    """Resolve category → latest reference version → CategorySchema + SeoConstraints, or None.

    Returns None (schema/constraints stay unavailable → constraint rules not_evaluated) when:
    the marketplace has no documented title limit (e.g. megamarket); the category cannot be
    resolved deterministically; there is no reference attribute data; or the latest reference
    version is STALE (older than REFERENCE_STALENESS_DAYS)."""
    if marketplace not in MARKETPLACE_TITLE_MAX_LEN:
        return None                                          # unsupported marketplace (e.g. megamarket)

    category = await _latest_category(db, marketplace, card_category)
    if category is None:
        return None                                          # unresolved category

    attrs, version, captured_at = await _latest_attributes(db, marketplace, category.category_id)
    if not attrs or version is None or captured_at is None:
        return None                                          # no reference attributes

    ts = now or datetime.utcnow()
    if captured_at < ts - timedelta(days=REFERENCE_STALENESS_DAYS):
        return None                                          # STALE reference — degrade honestly

    required = tuple(a.name for a in attrs if a.is_required)
    filterable = tuple(a.name for a in attrs if a.is_filterable)
    variant = tuple(a.name for a in attrs if a.is_variant)

    schema = CategorySchema(
        required_attributes=required, filterable_attributes=filterable, variant_attributes=variant)

    expected_path = tuple(p.strip() for p in category.path.split(">")) if category.path else None

    constraints = SeoConstraints(
        title_min_len=PULT_TITLE_MIN_LEN,
        title_max_len=MARKETPLACE_TITLE_MAX_LEN[marketplace],   # marketplace-sourced hard limit
        description_min_len=PULT_DESCRIPTION_MIN_LEN,
        media_min_images=PULT_MEDIA_MIN_IMAGES,
        attribute_fill_rate_threshold=PULT_ATTRIBUTE_FILL_RATE_THRESHOLD,
        content_completeness_threshold=PULT_CONTENT_COMPLETENESS_THRESHOLD)

    return ReferenceMerge(
        category_schema=schema, expected_category_path=expected_path,
        constraints=constraints, variant_attribute_keys=variant, reference_version=version)
