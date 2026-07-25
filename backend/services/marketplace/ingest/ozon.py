"""Ozon API ingestion (PULT-LAUNCH-1.4.5F).

The Ozon Seller API turned into store-aware, idempotent rows bound to the seller's existing
Account/Store/Product. Same discipline as the WB provider, Ozon endpoints:

  * products — POST /v3/product/list (last_id) + /v3/product/info/list. product_id is the Product's
    external id and authoritative identity; offer_id is the within-cabinet SKU; the title is never
    identity. Creates/reuses one Product + one ProductPlacement for the cabinet's primary store, and
    stores an ImportedCardContentRow + an ImportedProductRow snapshot.
  * prices — POST /v5/product/info/prices → ImportedProductRow.price (Decimal; unknown = NULL).
  * stocks — POST /v4/product/info/stocks → ImportedProductRow.stock. FBO and FBS stock entries are
    summed ONCE per product (never double-counted); a refresh REPLACES the snapshot and never wipes
    the price.
  * fbo_postings / fbs_postings — /v2/posting/fbo/list, /v3/posting/fbs/list → MarketplaceOperation
    (operation_type='order'). The delivery scheme is namespaced into external_operation_id
    ('fbo:'/'fbs:') so FBO and FBS never collide, and recorded in provider_operation_code.
  * finance — POST /v3/finance/transaction/list. Each operation → a primary MarketplaceOperation
    plus one per money service (commission/logistics/…), each signed and separately keyed.
  * returns — POST /v1/returns/list → MarketplaceOperation(return), only from the official returns
    endpoint (a return is never a cancellation).

A posting is an order lifecycle; a finance operation is a money event — the two are never counted
as two sales, and nothing here enters a user total until the source policy (1.4.5H). Money is
Decimal, never float; an unknown amount is NULL; no buyer PII, no raw payload.
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
from services.marketplace.ozon_client import ozon_client

MARKETPLACE = "ozon"
DATA_TYPES = ("products", "prices", "stocks", "fbo_postings", "fbs_postings", "finance", "returns")

_PAGE = 1000
_EPOCH = "2019-06-20T00:00:00.000Z"


def _s(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _dec(value) -> Optional[Decimal]:
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


async def _index(db, account_id: str) -> AccountProductIndex:
    products = (await db.execute(
        select(Product).where(Product.marketplace_account_id == account_id))).scalars().all()
    return AccountProductIndex(products, account_id)


def _owner(state) -> str:
    return getattr(state, "_owner_user_id", "") or ""


async def _resolve(db, index: AccountProductIndex, ext: Optional[str], sku: Optional[str]) -> Optional[str]:
    if ext is None and sku is None:
        return None
    res = index.resolve(external_product_id=ext, sku=sku)
    return res.product_id if res.status == FOUND else None


async def _ensure_placement(db, product_id: str, account_id: str, store_id: str) -> None:
    existing = (await db.execute(
        select(ProductPlacement).where(
            ProductPlacement.marketplace_store_id == store_id,
            ProductPlacement.product_id == product_id))).scalars().first()
    if existing is not None:
        existing.last_seen_at = datetime.utcnow()
        return
    db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=product_id,
                            marketplace_store_id=store_id, marketplace_account_id=account_id,
                            status="active", source="api"))


async def _upsert_operation(db, state, *, external_operation_id, operation_type, provider_code=None,
                            parent=None, product_id=None, occurred_at=None, status=None,
                            quantity=None, amount=None, currency=None, now: datetime) -> None:
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
    row.external_parent_id = parent
    row.provider_operation_code = provider_code
    row.product_id = product_id
    row.occurred_at = occurred_at
    row.status = status
    row.quantity = quantity
    row.amount = amount
    row.currency = currency
    row.fetched_at = now


# ── Page drivers ────────────────────────────────────────────────────────────────

async def fetch_and_persist_page(db, state, token: str, client_id: Optional[str] = None) -> dict:
    """One step for state.data_type, page + cursor committed together by the scheduler."""
    kw = {"token": token, "client_id": client_id}
    if state.data_type == "products":
        return await _page_products(db, state, **kw)
    if state.data_type == "prices":
        return await _page_prices(db, state, **kw)
    if state.data_type == "stocks":
        return await _page_stocks(db, state, **kw)
    if state.data_type == "fbo_postings":
        return await _page_postings(db, state, scheme="fbo", **kw)
    if state.data_type == "fbs_postings":
        return await _page_postings(db, state, scheme="fbs", **kw)
    if state.data_type == "finance":
        return await _page_finance(db, state, **kw)
    if state.data_type == "returns":
        return await _page_returns(db, state, **kw)
    raise ValueError(f"unsupported Ozon data_type: {state.data_type}")


async def _page_products(db, state, *, token, client_id) -> dict:
    now = datetime.utcnow()
    last_id = json.loads(state.cursor).get("last_id") if state.cursor else ""
    page = await ozon_client.list_products(token=token, client_id=client_id, last_id=last_id or "")
    result = page.get("result") or {}
    items = result.get("items") or []
    ids = [it.get("product_id") for it in items if isinstance(it, dict) and it.get("product_id")]
    details = await ozon_client.product_info_list(token=token, client_id=client_id, product_ids=ids) if ids else []
    detail_by_id = {str(d.get("id") or d.get("product_id")): d for d in details if isinstance(d, dict)}

    index = await _index(db, state.marketplace_account_id)
    for it in items:
        if not isinstance(it, dict):
            continue
        pid_ext = _s(it.get("product_id"))
        offer = _s(it.get("offer_id"))
        if pid_ext is None:
            continue
        d = detail_by_id.get(pid_ext, {})
        title = _s(d.get("name"))
        product_id = await _resolve(db, index, pid_ext, offer)
        if product_id is None:
            product = Product(id=str(uuid.uuid4()), user_id=_owner(state),
                              name=title or offer or pid_ext, marketplace=MARKETPLACE, sku=offer,
                              marketplace_account_id=state.marketplace_account_id,
                              external_product_id=pid_ext)
            db.add(product); await db.flush(); index.add(product)
            product_id = product.id
        await _ensure_placement(db, product_id, state.marketplace_account_id, state.marketplace_store_id)

        card = (await db.execute(select(ImportedCardContentRow).where(
            ImportedCardContentRow.marketplace_account_id == state.marketplace_account_id,
            ImportedCardContentRow.source == "api",
            ImportedCardContentRow.external_row_id == pid_ext))).scalars().first()
        if card is None:
            card = ImportedCardContentRow(
                id=str(uuid.uuid4()), import_id=state.id, user_id=_owner(state),
                marketplace=MARKETPLACE, source="api", external_row_id=pid_ext,
                marketplace_account_id=state.marketplace_account_id)
            db.add(card)
        card.sku = offer
        card.title = title
        card.description = _s(d.get("description"))
        card.brand = None
        card.category = _s(d.get("type_name") or d.get("category"))
        card.product_id = product_id
        card.link_status = "linked"
        card.fetched_at = now

    next_last = result.get("last_id")
    done = not items or not next_last
    state.cursor = None if done else json.dumps({"last_id": next_last})
    return {"done": done, "count": len(items), "defer": False}


async def _snapshot_row(db, state, ext: str) -> ImportedProductRow:
    row = (await db.execute(select(ImportedProductRow).where(
        ImportedProductRow.marketplace_account_id == state.marketplace_account_id,
        ImportedProductRow.marketplace_store_id == state.marketplace_store_id,
        ImportedProductRow.source == "api",
        ImportedProductRow.external_row_id == ext))).scalars().first()
    if row is None:
        row = ImportedProductRow(
            id=str(uuid.uuid4()), import_id=state.id, user_id=_owner(state),
            marketplace=MARKETPLACE, source="api", external_row_id=ext, sku=ext,
            marketplace_account_id=state.marketplace_account_id,
            marketplace_store_id=state.marketplace_store_id)
        db.add(row)
    return row


async def _page_prices(db, state, *, token, client_id) -> dict:
    now = datetime.utcnow()
    last_id = json.loads(state.cursor).get("last_id") if state.cursor else ""
    page = await ozon_client.product_prices(token=token, client_id=client_id, last_id=last_id or "")
    result = page.get("result") or {}
    items = result.get("items") or []
    index = await _index(db, state.marketplace_account_id)
    for it in items:
        if not isinstance(it, dict):
            continue
        ext = _s(it.get("product_id"))
        if ext is None:
            continue
        price_obj = it.get("price") if isinstance(it.get("price"), dict) else {}
        price = _dec(price_obj.get("price"))     # NULL if absent; never 0
        row = await _snapshot_row(db, state, ext)
        row.price = price                        # stock is NOT touched here
        row.product_id = await _resolve(db, index, ext, _s(it.get("offer_id")))
        row.link_status = "linked" if row.product_id else "unassigned"
        row.fetched_at = now
    next_last = result.get("last_id")
    done = not items or not next_last
    state.cursor = None if done else json.dumps({"last_id": next_last})
    return {"done": done, "count": len(items), "defer": False}


async def _page_stocks(db, state, *, token, client_id) -> dict:
    now = datetime.utcnow()
    last_id = json.loads(state.cursor).get("last_id") if state.cursor else ""
    page = await ozon_client.product_stocks(token=token, client_id=client_id, last_id=last_id or "")
    result = page.get("result") or {}
    items = result.get("items") or []
    index = await _index(db, state.marketplace_account_id)
    for it in items:
        if not isinstance(it, dict):
            continue
        ext = _s(it.get("product_id"))
        if ext is None:
            continue
        # Sum each stock type ONCE (fbo + fbs), so a product present in both schemes is not
        # double-counted. An absent quantity contributes nothing; a fully-absent stock stays NULL.
        total = None
        for s in (it.get("stocks") or []):
            if isinstance(s, dict) and s.get("present") is not None:
                total = (total or 0) + (_int(s.get("present")) or 0)
        row = await _snapshot_row(db, state, ext)
        row.stock = total                        # price is NOT touched here
        row.product_id = await _resolve(db, index, ext, _s(it.get("offer_id")))
        row.link_status = "linked" if row.product_id else "unassigned"
        row.fetched_at = now
    next_last = result.get("last_id")
    done = not items or not next_last
    state.cursor = None if done else json.dumps({"last_id": next_last})
    return {"done": done, "count": len(items), "defer": False}


async def _page_postings(db, state, *, scheme, token, client_id) -> dict:
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    since = cur.get("since") or _EPOCH
    to = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    offset = cur.get("offset") or 0
    fetch = ozon_client.posting_fbo_list if scheme == "fbo" else ozon_client.posting_fbs_list
    rows = await fetch(token=token, client_id=client_id, since=since, to=to,
                       offset=offset, limit=_PAGE)
    index = await _index(db, state.marketplace_account_id)
    for r in rows:
        if not isinstance(r, dict):
            continue
        posting = _s(r.get("posting_number"))
        if posting is None:
            continue
        products = r.get("products") or []
        product_id = None
        if len(products) == 1 and isinstance(products[0], dict):
            product_id = await _resolve(db, index, None, _s(products[0].get("offer_id")))
        qty = sum((_int(p.get("quantity")) or 0) for p in products if isinstance(p, dict)) or None
        # A posting is an ORDER lifecycle, never a sale; FBO/FBS namespaced so they never collide.
        await _upsert_operation(
            db, state, external_operation_id=f"{scheme}:{posting}", operation_type="order",
            provider_code=scheme, product_id=product_id, occurred_at=_dt(r.get("in_process_at")),
            status=_s(r.get("status")), quantity=qty, amount=None, currency=None, now=now)
    done = len(rows) < _PAGE
    state.cursor = json.dumps({"since": since, "offset": 0 if done else offset + len(rows)})
    return {"done": done, "count": len(rows), "defer": False}


_FIN_SERVICE = (("комисс", "commission"), ("commission", "commission"),
                ("логист", "logistics"), ("logist", "logistics"), ("deliv", "logistics"),
                ("штраф", "penalty"), ("penalt", "penalty"))


def _service_type(name: Optional[str]) -> str:
    low = (name or "").lower()
    for kw, t in _FIN_SERVICE:
        if kw in low:
            return t
    return "deduction"


async def _page_finance(db, state, *, token, client_id) -> dict:
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    date_from = cur.get("from") or "2024-01-01T00:00:00.000Z"
    date_to = cur.get("to") or now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    page = cur.get("page") or 1
    data = await ozon_client.finance_transactions(
        token=token, client_id=client_id, date_from=date_from, date_to=date_to, page=page)
    result = data.get("result") or {}
    ops = result.get("operations") or []
    index = await _index(db, state.marketplace_account_id)
    count = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        oid = _s(op.get("operation_id"))
        if oid is None:
            continue
        posting = op.get("posting") if isinstance(op.get("posting"), dict) else {}
        parent = _s(posting.get("posting_number"))
        items = op.get("items") or []
        sku = _s(items[0].get("sku")) if items and isinstance(items[0], dict) else None
        product_id = await _resolve(db, index, sku, None)
        op_name = _s(op.get("operation_type_name") or op.get("operation_type"))
        low = (op_name or "").lower() + " " + (_s(op.get("type")) or "").lower()
        primary = "return" if "возврат" in low or "return" in low else \
                  ("sale" if any(k in low for k in ("продаж", "sale", "orders", "достав")) else "other")
        await _upsert_operation(
            db, state, external_operation_id=oid, operation_type=primary, provider_code=op_name,
            parent=parent, product_id=product_id, occurred_at=_dt(op.get("operation_date")),
            amount=_dec(op.get("amount")), currency=_s(op.get("currency_code")), now=now)
        count += 1
        for svc in (op.get("services") or []):
            if not isinstance(svc, dict):
                continue
            val = _dec(svc.get("price"))
            if val is None:
                continue
            comp = _service_type(_s(svc.get("name")))
            await _upsert_operation(
                db, state, external_operation_id=f"{oid}:{comp}:{_s(svc.get('name'))}",
                operation_type=comp, provider_code=_s(svc.get("name")), parent=oid,
                product_id=product_id, occurred_at=_dt(op.get("operation_date")),
                amount=val, currency=_s(op.get("currency_code")), now=now)
            count += 1
    page_count = result.get("page_count") or 1
    done = page >= page_count
    state.covered_from, state.covered_to = date_from[:10], date_to[:10]
    state.cursor = json.dumps({"from": date_from, "to": date_to, "page": page + 1}) if not done \
        else json.dumps({"from": date_from, "to": date_to})
    return {"done": done, "count": count, "defer": False}


async def _page_returns(db, state, *, token, client_id) -> dict:
    now = datetime.utcnow()
    last_id = json.loads(state.cursor).get("last_id") if state.cursor else 0
    data = await ozon_client.returns_list(token=token, client_id=client_id, last_id=int(last_id or 0))
    rows = data.get("returns") or data.get("result") or []
    rows = rows if isinstance(rows, list) else []
    index = await _index(db, state.marketplace_account_id)
    max_id = int(last_id or 0)
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = _s(r.get("id"))
        if rid is None:
            continue
        prod = r.get("product") if isinstance(r.get("product"), dict) else {}
        product_id = await _resolve(db, index, _s(prod.get("sku")), _s(prod.get("offer_id")))
        # A return comes ONLY from the official returns endpoint — never inferred from a cancellation.
        await _upsert_operation(
            db, state, external_operation_id=rid, operation_type="return",
            provider_code="return", parent=_s(r.get("posting_number")), product_id=product_id,
            occurred_at=_dt(r.get("created_at") or r.get("accepted_at")),
            quantity=_int(prod.get("quantity")), amount=_dec(r.get("price") or (prod or {}).get("price")),
            currency=None, now=now)
        rid_int = _int(rid)
        if rid_int and rid_int > max_id:
            max_id = rid_int
    done = not bool(data.get("has_next"))
    state.cursor = None if done else json.dumps({"last_id": max_id})
    return {"done": done, "count": len(rows), "defer": False}
