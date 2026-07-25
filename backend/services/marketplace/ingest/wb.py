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
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select

from models.imported_card_content import ImportedCardContentRow
from models.imported_product import ImportedProductRow
from models.marketplace_operation import MarketplaceOperation
from models.product import Product
from models.product_placement import ProductPlacement
from services.account_product_resolver import FOUND, AccountProductIndex
from services.marketplace.wb_client import wb_client

MARKETPLACE = "wildberries"
DATA_TYPES = ("card_content", "prices", "orders", "sales", "stocks", "finance")

_CARD_PAGE = 100
_PRICE_PAGE = 1000

# WB financial operation name → normalized operation_type. Anything not here is kept as 'other'
# with the official name in provider_operation_code, so a new WB operation is never lost.
_FINANCE_TYPE = {
    "продажа": "sale",
    "возврат": "return",
    "логистика": "logistics",
    "штраф": "penalty",
    "удержание": "deduction",
    "коррекция": "deduction",
}


def _dec(value) -> Optional[Decimal]:
    """A signed money value as Decimal, or None. Never float, never coerced to 0."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dt(value) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "").split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


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


async def _resolve_product(db, index: AccountProductIndex, state, nm: Optional[str],
                           sku: Optional[str] = None) -> Optional[str]:
    """A product id for an operation: nmID (external id) wins, then account SKU, never the title.
    An ambiguous SKU is left unlinked — never the first match."""
    if nm is None and sku is None:
        return None
    res = index.resolve(external_product_id=nm, sku=sku)
    return res.product_id if res.status == FOUND else None


async def _upsert_operation(db, state, *, external_operation_id: str, operation_type: str,
                            provider_dataset: str,
                            provider_code: Optional[str] = None, parent: Optional[str] = None,
                            product_id: Optional[str] = None, occurred_at: Optional[datetime] = None,
                            status: Optional[str] = None, quantity: Optional[int] = None,
                            amount: Optional[Decimal] = None, currency: Optional[str] = None,
                            now: datetime) -> None:
    """Insert or update ONE operation, idempotent on (account, source, external id, type).

    `provider_dataset` records the feed this row came from (orders|sales|finance) so the source-policy
    money reader counts a sale from exactly one feed — WB reports the same sale in both sales and
    finance, and only one is the authoritative money source."""
    row = (await db.execute(
        select(MarketplaceOperation).where(
            MarketplaceOperation.marketplace_account_id == state.marketplace_account_id,
            MarketplaceOperation.source == "api",
            MarketplaceOperation.external_operation_id == external_operation_id,
            MarketplaceOperation.operation_type == operation_type))).scalars().first()
    if row is None:
        row = MarketplaceOperation(
            id=str(uuid.uuid4()), marketplace_account_id=state.marketplace_account_id,
            marketplace_store_id=state.marketplace_store_id, marketplace=MARKETPLACE, source="api",
            external_operation_id=external_operation_id, operation_type=operation_type)
        db.add(row)
    row.provider_dataset = provider_dataset
    row.external_parent_id = parent
    row.provider_operation_code = provider_code
    row.product_id = product_id
    row.occurred_at = occurred_at
    row.status = status
    row.quantity = quantity
    row.amount = amount
    row.currency = currency
    row.fetched_at = now


# ── Page drivers (one page, one transaction; cursor moves only on commit) ───────

async def fetch_and_persist_page(db, state, token: str, client_id=None) -> dict:
    """Pull ONE step for state.data_type, persist it and the advanced cursor in one transaction.

    Returns {"done": bool, "count": int, "defer": bool}. `done` True means the type is fully synced;
    `defer` True (stocks async report not ready) means "come back later", nothing more to do now.
    Raising leaves the transaction to be rolled back, so the cursor never advances past unwritten
    rows. `client_id` is accepted for a uniform provider signature (WB does not use it)."""
    if state.data_type == "card_content":
        return await _page_cards(db, state, token)
    if state.data_type == "prices":
        return await _page_prices(db, state, token)
    if state.data_type == "orders":
        return await _page_orders(db, state, token)
    if state.data_type == "sales":
        return await _page_sales(db, state, token)
    if state.data_type == "stocks":
        return await _page_stocks(db, state, token)
    if state.data_type == "finance":
        return await _page_finance(db, state, token)
    raise ValueError(f"unsupported WB data_type: {state.data_type}")


_EPOCH = "2019-06-20T00:00:00"   # WB statistics accept an early dateFrom for a first full pull


async def _page_orders(db, state, token: str) -> dict:
    now = datetime.utcnow()
    date_from = (json.loads(state.cursor).get("lastChangeDate") if state.cursor else None) or _EPOCH
    rows = await wb_client.list_orders(token=token, date_from=date_from)
    index = await _load_index(db, state.marketplace_account_id)
    max_change = date_from
    for r in rows:
        if not isinstance(r, dict):
            continue
        srid = _s(r.get("srid"))
        if srid is None:
            continue
        nm = _s(r.get("nmId") or r.get("nmID"))
        product_id = await _resolve_product(db, index, state, nm)
        # An order is NOT a sale and NOT revenue: it is stored as an 'order' event; a cancelled order
        # is a status change, never a return.
        await _upsert_operation(
            db, state, external_operation_id=srid, operation_type="order",
            provider_dataset="orders", provider_code="order", product_id=product_id,
            occurred_at=_dt(r.get("date")), status="cancelled" if r.get("isCancel") else "new",
            quantity=1, amount=_dec(r.get("priceWithDisc") or r.get("totalPrice")),
            currency="RUB" if r.get("priceWithDisc") is not None else None, now=now)
        lc = _s(r.get("lastChangeDate"))
        if lc and lc > max_change:
            max_change = lc
    state.cursor = json.dumps({"lastChangeDate": max_change})
    return {"done": True, "count": len(rows), "defer": False}


async def _page_sales(db, state, token: str) -> dict:
    now = datetime.utcnow()
    date_from = (json.loads(state.cursor).get("lastChangeDate") if state.cursor else None) or _EPOCH
    rows = await wb_client.list_sales(token=token, date_from=date_from)
    index = await _load_index(db, state.marketplace_account_id)
    max_change = date_from
    for r in rows:
        if not isinstance(r, dict):
            continue
        sale_id = _s(r.get("saleID"))
        srid = _s(r.get("srid"))
        if sale_id is None:
            continue
        nm = _s(r.get("nmId") or r.get("nmID"))
        product_id = await _resolve_product(db, index, state, nm)
        # Type from the OFFICIAL flag, never from the amount's sign: saleID starts with 'S' for a
        # sale and 'R' for a return; isCancel marks a cancellation.
        if r.get("isCancel"):
            op_type = "cancellation"
        elif sale_id.upper().startswith("R"):
            op_type = "return"
        elif sale_id.upper().startswith("S"):
            op_type = "sale"
        else:
            op_type = "other"
        await _upsert_operation(
            db, state, external_operation_id=sale_id, operation_type=op_type,
            provider_dataset="sales", provider_code=_s(r.get("saleID")), parent=srid, product_id=product_id,
            occurred_at=_dt(r.get("date")), status=None, quantity=1,
            amount=_dec(r.get("forPay")),
            currency="RUB" if r.get("forPay") is not None else None, now=now)
        lc = _s(r.get("lastChangeDate"))
        if lc and lc > max_change:
            max_change = lc
    state.cursor = json.dumps({"lastChangeDate": max_change})
    return {"done": True, "count": len(rows), "defer": False}


async def _page_finance(db, state, token: str) -> dict:
    """The NEW Finance API (the retired v5 reportDetailByPeriod is never called). Each report row
    becomes one OR MORE operations — one per money component present — each signed and typed."""
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    date_from = cur.get("from") or "2024-01-29"
    date_to = cur.get("to") or now.strftime("%Y-%m-%d")
    rows = await wb_client.finance_sales_report_detailed(token=token, date_from=date_from, date_to=date_to)
    index = await _load_index(db, state.marketplace_account_id)
    count = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        rrd = _s(r.get("rrd_id") or r.get("rrdId"))
        if rrd is None:
            continue
        srid = _s(r.get("srid"))
        nm = _s(r.get("nm_id") or r.get("nmId"))
        product_id = await _resolve_product(db, index, state, nm)
        occurred = _dt(r.get("rr_dt") or r.get("order_dt") or r.get("sale_dt"))
        oper_name = _s(r.get("supplier_oper_name"))
        # The primary operation (sale / return / other) from the official operation name.
        primary = _FINANCE_TYPE.get((oper_name or "").lower(), "other")
        await _upsert_operation(
            db, state, external_operation_id=rrd, operation_type=primary,
            provider_dataset="finance", provider_code=oper_name, parent=srid, product_id=product_id, occurred_at=occurred,
            quantity=_int(r.get("quantity")), amount=_dec(r.get("retail_amount") or r.get("ppvz_for_pay")),
            currency=_s(r.get("currency_name")), now=now)
        count += 1
        # Each money component of the SAME report row is a distinct, separately-keyed operation, so
        # commission and logistics never overwrite each other.
        for field, op_type in (("commission_amount", "commission"),
                               ("delivery_amount", "logistics"),
                               ("penalty", "penalty"),
                               ("deduction", "deduction")):
            val = _dec(r.get(field))
            if val is None:
                continue
            await _upsert_operation(
                db, state, external_operation_id=f"{rrd}:{op_type}", operation_type=op_type,
                provider_dataset="finance", provider_code=oper_name, parent=rrd, product_id=product_id, occurred_at=occurred,
                amount=val, currency=_s(r.get("currency_name")), now=now)
            count += 1
    state.covered_from = date_from
    state.covered_to = date_to
    state.cursor = json.dumps({"from": date_from, "to": date_to})
    return {"done": True, "count": count, "defer": False}


async def _page_stocks(db, state, token: str) -> dict:
    """Current-day stock snapshot via the async warehouse_remains report, across scheduler ticks:
    create → (defer while processing) → download → sum warehouses per nmID → ImportedProductRow.stock.
    The report is downloaded into memory and parsed; no file is written to uploads and nothing is
    logged."""
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    task_id = cur.get("taskId")

    if not task_id:
        task_id = await wb_client.create_warehouse_remains_report(token=token)
        state.cursor = json.dumps({"taskId": task_id})
        return {"done": False, "count": 0, "defer": True}

    status = await wb_client.warehouse_remains_status(token=token, task_id=task_id)
    if status != "done":
        return {"done": False, "count": 0, "defer": True}

    rows = await wb_client.download_warehouse_remains(token=token, task_id=task_id)
    # Sum quantity across warehouses/sizes per nmID → one snapshot value per product. A repeat
    # REPLACES the snapshot (UPDATE), never adds — so a re-download does not inflate stock.
    per_nm: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        nm = _s(r.get("nmId") or r.get("nmID"))
        if nm is None:
            continue
        qty = _int(r.get("quantity")) or 0
        per_nm[nm] = per_nm.get(nm, 0) + qty

    index = await _load_index(db, state.marketplace_account_id)
    for nm, qty in per_nm.items():
        product_id = await _resolve_product(db, index, state, nm)
        row = (await db.execute(
            select(ImportedProductRow).where(
                ImportedProductRow.marketplace_account_id == state.marketplace_account_id,
                ImportedProductRow.marketplace_store_id == state.marketplace_store_id,
                ImportedProductRow.source == "api",
                ImportedProductRow.external_row_id == nm))).scalars().first()
        if row is None:
            row = ImportedProductRow(
                id=str(uuid.uuid4()), import_id=state.id, user_id=_owner(state),
                marketplace=MARKETPLACE, source="api", external_row_id=nm, sku=nm,
                marketplace_account_id=state.marketplace_account_id,
                marketplace_store_id=state.marketplace_store_id)
            db.add(row)
        # Only the stock is (re)written — a stock refresh must NOT wipe an API price captured by the
        # prices sync.
        row.stock = qty
        row.product_id = product_id
        row.link_status = "linked" if product_id else "unassigned"
        row.fetched_at = now

    state.cursor = None   # snapshot consumed; next run starts a fresh report
    return {"done": True, "count": len(per_nm), "defer": False}


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
