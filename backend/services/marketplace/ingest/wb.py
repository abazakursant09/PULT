"""Wildberries API ingestion (PULT-LAUNCH-1.4.5E).

Turns the two officially-confirmed WB read endpoints into store-aware, idempotent Imported* rows:

  * card_content — POST /content/v2/get/cards/list (cursor updatedAt+nmID). The nmID is the
    Product's external id; the vendorCode is the seller SKU inside the cabinet. A card creates or
    reuses ONE Product and ONE ProductPlacement for the WB store, then stores an
    ImportedCardContentRow (title / description / characteristics / photos). The name is NEVER an
    identity; an ambiguous SKU (only possible when the nmID is absent) is left unassigned, never
    resolved by picking the first match.

  * prices — GET /api/v2/list/goods/filter (offset). Each nmID's price becomes an
    ImportedProductRow snapshot; a field WB does not return stays NULL, never 0.

Idempotency is the DB's job: every API row carries external_row_id = nmID, and a partial unique
over (account, [store,] source, external_row_id) makes a repeated page or a repeated full sync
UPDATE the row in place. The page rows AND the cursor advance in ONE transaction, so a failed
commit rolls both back and the cursor never moves past unwritten data.

This module writes nothing a user total reads yet (source='api', isolated until 1.4.5H) and calls
no marketplace unless the scheduler is enabled.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from models.imported_card_content import ImportedCardContentRow
from models.imported_product import ImportedProductRow
from models.product import Product
from models.product_placement import ProductPlacement
from services.account_product_resolver import FOUND, AccountProductIndex
from services.marketplace.wb_client import wb_client

MARKETPLACE = "wildberries"
DATA_TYPES = ("card_content", "prices")

_CARD_PAGE = 100
_PRICE_PAGE = 1000


def _s(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


async def _load_index(db, account_id: str) -> AccountProductIndex:
    products = (await db.execute(
        select(Product).where(Product.marketplace_account_id == account_id))).scalars().all()
    return AccountProductIndex(products, account_id)


async def _ensure_placement(db, product_id: str, account_id: str, store_id: str, source: str) -> None:
    """One ProductPlacement per (store, product). A repeat never inserts a second."""
    existing = (await db.execute(
        select(ProductPlacement).where(
            ProductPlacement.marketplace_store_id == store_id,
            ProductPlacement.product_id == product_id))).scalars().first()
    if existing is not None:
        existing.last_seen_at = datetime.utcnow()
        return
    db.add(ProductPlacement(
        id=str(uuid.uuid4()), product_id=product_id, marketplace_store_id=store_id,
        marketplace_account_id=account_id, status="active", source=source))


async def _upsert_card(db, state, index: AccountProductIndex, card: dict, now: datetime) -> None:
    """Normalize one WB card into identity + an ImportedCardContentRow. Idempotent by nmID."""
    nm = _s(card.get("nmID"))
    if nm is None:
        return   # a card with no nmID has no stable identity; skip rather than guess
    vendor = _s(card.get("vendorCode"))
    title = _s(card.get("title"))

    # Identity is the nmID, which WB always carries. It is resolved by external id ONLY: an nmID is
    # an authoritative key, so a SKU that happens to be ambiguous never blocks it and never causes
    # the wrong product to be picked. The title is never identity.
    res = index.resolve(external_product_id=nm)
    product_id: Optional[str] = None
    link_status = "unassigned"
    if res.status == FOUND:
        product_id, link_status = res.product_id, "linked"
    else:
        # No product carries this nmID yet: create one, pinned by its nmID, from safe fields only.
        product = Product(
            id=str(uuid.uuid4()), user_id=str(_owner(state)), name=title or vendor or nm,
            marketplace=MARKETPLACE, sku=vendor, marketplace_account_id=state.marketplace_account_id,
            external_product_id=nm)
        db.add(product)
        await db.flush()
        index.add(product)
        product_id, link_status = product.id, "linked"

    if product_id is not None:
        await _ensure_placement(db, product_id, state.marketplace_account_id,
                                state.marketplace_store_id, source="api")

    characteristics = card.get("characteristics") or card.get("addin") or None
    photos = card.get("photos") or []
    image_count = len(photos) if isinstance(photos, list) else None

    row = (await db.execute(
        select(ImportedCardContentRow).where(
            ImportedCardContentRow.marketplace_account_id == state.marketplace_account_id,
            ImportedCardContentRow.source == "api",
            ImportedCardContentRow.external_row_id == nm))).scalars().first()
    if row is None:
        row = ImportedCardContentRow(
            id=str(uuid.uuid4()), import_id=state.id, user_id=str(_owner(state)),
            marketplace=MARKETPLACE, source="api", external_row_id=nm,
            marketplace_account_id=state.marketplace_account_id)
        db.add(row)
    row.sku = vendor
    row.title = title
    row.description = _s(card.get("description"))
    row.brand = _s(card.get("brand"))
    row.category = _s(card.get("subjectName") or card.get("object"))
    row.characteristics_json = json.dumps(characteristics, ensure_ascii=False) if characteristics else None
    row.image_count = image_count
    row.image_urls_json = json.dumps(photos, ensure_ascii=False) if photos else None
    row.product_id = product_id
    row.link_status = link_status
    row.fetched_at = now


async def _upsert_price(db, state, index: AccountProductIndex, good: dict, now: datetime) -> None:
    """One WB price row → an ImportedProductRow snapshot. Idempotent by nmID; unknown stays NULL."""
    nm = _s(good.get("nmID"))
    if nm is None:
        return
    # WB list/goods/filter nests the sizes; the price lives on the good or its first size. Read only
    # what is actually present — never invent a 0.
    price = _price_of(good)

    res = index.resolve(external_product_id=nm)
    product_id = res.product_id if res.status == FOUND else None
    link_status = "linked" if product_id else "unassigned"

    row = (await db.execute(
        select(ImportedProductRow).where(
            ImportedProductRow.marketplace_account_id == state.marketplace_account_id,
            ImportedProductRow.marketplace_store_id == state.marketplace_store_id,
            ImportedProductRow.source == "api",
            ImportedProductRow.external_row_id == nm))).scalars().first()
    if row is None:
        row = ImportedProductRow(
            id=str(uuid.uuid4()), import_id=state.id, user_id=str(_owner(state)),
            marketplace=MARKETPLACE, source="api", external_row_id=nm, sku=nm,
            marketplace_account_id=state.marketplace_account_id,
            marketplace_store_id=state.marketplace_store_id)
        db.add(row)
    row.price = price            # NULL if WB did not report it — never coerced to 0
    row.product_id = product_id
    row.link_status = link_status
    row.fetched_at = now


def _price_of(good: dict) -> Optional[float]:
    for size in (good.get("sizes") or []):
        if isinstance(size, dict) and size.get("price") is not None:
            try:
                return float(size["price"])
            except (TypeError, ValueError):
                return None
    for key in ("price", "discountedPrice"):
        if good.get(key) is not None:
            try:
                return float(good[key])
            except (TypeError, ValueError):
                return None
    return None


def _owner(state) -> str:
    # The Imported* rows carry user_id for the existing fast-path queries. It is stamped from the
    # connection's user_id, threaded onto the state by the scheduler.
    return getattr(state, "_owner_user_id", "") or ""


# ── Page drivers (one page, one transaction; cursor moves only on commit) ───────

async def fetch_and_persist_page(db, state, token: str) -> dict:
    """Pull ONE page for state.data_type, persist it and the advanced cursor in one transaction.

    Returns {"done": bool, "count": int}. `done` True means the last page was reached. Raising
    (transport / auth / rate limit) leaves the transaction to be rolled back by the caller, so the
    cursor never advances past unwritten rows.
    """
    if state.data_type == "card_content":
        return await _page_cards(db, state, token)
    if state.data_type == "prices":
        return await _page_prices(db, state, token)
    raise ValueError(f"unsupported WB data_type: {state.data_type}")


async def _page_cards(db, state, token: str) -> dict:
    now = datetime.utcnow()
    cursor = json.loads(state.cursor) if state.cursor else None
    data = await wb_client.list_cards(token=token, cursor=cursor, limit=_CARD_PAGE)
    cards = data.get("cards") or []
    index = await _load_index(db, state.marketplace_account_id)
    for card in cards:
        if isinstance(card, dict):
            await _upsert_card(db, state, index, card, now)

    next_cur = data.get("cursor") or {}
    total = next_cur.get("total")
    done = not cards or (isinstance(total, int) and total < _CARD_PAGE)
    # Persist the resume cursor in the SAME transaction as the rows.
    state.cursor = json.dumps({"updatedAt": next_cur.get("updatedAt"), "nmID": next_cur.get("nmID")}) \
        if not done and next_cur else None
    return {"done": done, "count": len(cards)}


async def _page_prices(db, state, token: str) -> dict:
    now = datetime.utcnow()
    offset = int(json.loads(state.cursor).get("offset")) if state.cursor else 0
    goods = await wb_client.list_prices(token=token, offset=offset, limit=_PRICE_PAGE)
    index = await _load_index(db, state.marketplace_account_id)
    for good in goods:
        if isinstance(good, dict):
            await _upsert_price(db, state, index, good, now)

    done = len(goods) < _PRICE_PAGE
    state.cursor = None if done else json.dumps({"offset": offset + len(goods)})
    return {"done": done, "count": len(goods)}
