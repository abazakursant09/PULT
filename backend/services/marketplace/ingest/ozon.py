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
from models.marketplace_price_observation import PROMO_KEY_SENTINEL
from models.product import Product
from models.product_placement import ProductPlacement
from services.account_product_resolver import FOUND, AccountProductIndex
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest.change_only import observe_price
from services.marketplace.ozon_client import ozon_client

MARKETPLACE = "ozon"
DATA_TYPES = ("products", "prices", "stocks", "fbo_postings", "fbs_postings", "finance", "returns",
              # PULT-LAUNCH-2.5D — proven price/promo EVIDENCE (change-only, via change_only). These
              # only ever WRITE observations; they never change a product's promotion state. Reached
              # only through run_api_sync_once, which makes ZERO calls while the feature flag is OFF.
              "price_observations", "promotions")

_PAGE = 1000
_ACTION_PAGE = 1000
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


def _cur(value) -> tuple[Optional[str], str]:
    """A currency is PROVEN only when the provider gives a well-formed 3-letter code. Anything else is
    unknown → (None, 'unknown'); we never substitute RUB or any default."""
    s = _s(value)
    if s and len(s) == 3 and s.isalpha():
        return s.upper(), "proven"
    return None, "unknown"


def _bool(value) -> Optional[bool]:
    """A provider flag is proven only when it is an actual boolean. Absent/other → None (unknown);
    NULL is never coerced to False."""
    return value if isinstance(value, bool) else None


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


async def _upsert_operation(db, state, *, external_operation_id, operation_type, provider_dataset,
                            provider_code=None,
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
    if state.data_type == "price_observations":
        return await _page_price_observations(db, state, **kw)
    if state.data_type == "promotions":
        return await _page_promotions(db, state, **kw)
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
            provider_dataset=f"{scheme}_postings", provider_code=scheme, product_id=product_id,
            occurred_at=_dt(r.get("in_process_at")),
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
            db, state, external_operation_id=oid, operation_type=primary, provider_dataset="finance",
            provider_code=op_name,
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
                operation_type=comp, provider_dataset="finance", provider_code=_s(svc.get("name")), parent=oid,
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
            provider_dataset="returns", provider_code="return", parent=_s(r.get("posting_number")),
            product_id=product_id,
            occurred_at=_dt(r.get("created_at") or r.get("accepted_at")),
            quantity=_int(prod.get("quantity")), amount=_dec(r.get("price") or (prod or {}).get("price")),
            currency=None, now=now)
        rid_int = _int(rid)
        if rid_int and rid_int > max_id:
            max_id = rid_int
    done = not bool(data.get("has_next"))
    state.cursor = None if done else json.dumps({"last_id": max_id})
    return {"done": done, "count": len(rows), "defer": False}


# ── Price / promotion OBSERVATIONS (PULT-LAUNCH-2.5D; change-only 2.5E-1) ─────────
# These assemble proven price/promotion evidence and delegate the write to change_only.observe_price,
# which appends a new immutable version ONLY when the evidence differs from the immediate latest of the
# series (else it bumps last_verified_at, ≥24h). ingest_run_id is minted once per full sync pass (stored
# in the cursor) so re-processing a page inside the SAME pass never double-writes. A value is stored ONLY
# when the provider proves it; otherwise it stays NULL and its name is recorded in missing_fields
# (unknown is never 0). Revenue, subsidy and commission-base are NEVER inferred here.


async def _upsert_observation(db, state, *, run_id: str, ext: str, observation_kind: str,
                              promotion_key: str, product_id: Optional[str], now: datetime,
                              **fields) -> None:
    # PULT-LAUNCH-2.5E-1 — change-only: a new append-only row is written ONLY when the evidence
    # differs from the immediate latest of the series; an unchanged repeat bumps last_verified_at (≥24h)
    # instead. A resolved row carries product_id (placement FK + resolution CHECK); unassigned stays NULL.
    await observe_price(
        db, run_id=run_id, account_id=state.marketplace_account_id,
        store_id=state.marketplace_store_id, ext=ext, observation_kind=observation_kind,
        promotion_key=promotion_key, product_id=product_id, source="api", now=now, fields=fields)


async def _page_price_observations(db, state, *, token, client_id) -> dict:
    """Catalog price EVIDENCE from POST /v5/product/info/prices → observation_kind='catalog'.

    Proven-field mapping (2.5D §2): price→buyer_price, old_price→catalog_price,
    marketing_seller_price→seller_promo_price, min_price→provider_min_price,
    auto_action_enabled→auto_action_enabled, currency_code→currency (proven only). marketing_price is
    IGNORED (removed from v5). commissions are not stored as a proven base. Every absent value stays
    NULL and is named in missing_fields."""
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    run_id = cur.get("run_id") or str(uuid.uuid4())
    last_id = cur.get("last_id") or ""
    page = await ozon_client.product_prices(token=token, client_id=client_id, last_id=last_id or "")
    result = page.get("result") if isinstance(page.get("result"), dict) else {}
    items = result.get("items") or []
    index = await _index(db, state.marketplace_account_id)
    for it in items:
        if not isinstance(it, dict):
            continue
        ext = _s(it.get("product_id"))
        if ext is None:
            continue
        p = it.get("price") if isinstance(it.get("price"), dict) else {}
        buyer_price = _dec(p.get("price"))
        catalog_price = _dec(p.get("old_price"))
        seller_promo_price = _dec(p.get("marketing_seller_price"))
        provider_min_price = _dec(p.get("min_price"))
        auto_action_enabled = _bool(p.get("auto_action_enabled"))
        currency, currency_status = _cur(p.get("currency_code") or it.get("currency_code"))
        missing = []
        if buyer_price is None:
            missing.append("price")
        if catalog_price is None:
            missing.append("old_price")
        if seller_promo_price is None:
            missing.append("marketing_seller_price")
        if provider_min_price is None:
            missing.append("min_price")
        if auto_action_enabled is None:
            missing.append("auto_action_enabled")
        if currency is None:
            missing.append("currency_code")
        product_id = await _resolve(db, index, ext, _s(it.get("offer_id")))
        await _upsert_observation(
            db, state, run_id=run_id, ext=ext, observation_kind="catalog",
            promotion_key=PROMO_KEY_SENTINEL, product_id=product_id, now=now,
            promotion_id=None, promotion_type=None, participation_status=None,
            catalog_price=catalog_price, buyer_price=buyer_price,
            seller_promo_price=seller_promo_price, provider_min_price=provider_min_price,
            auto_action_enabled=auto_action_enabled,
            currency=currency, currency_status=currency_status,
            # never inferred from a buyer-facing price:
            expected_seller_revenue=None, seller_revenue_status="unknown",
            commission_base=None, commission_base_status="unknown",
            marketplace_subsidy=None, subsidy_status="unknown",
            provider_dataset="prices", missing_fields=missing)
    next_last = result.get("last_id")
    done = not items or not next_last
    state.cursor = None if done else json.dumps({"run_id": run_id, "last_id": next_last})
    return {"done": done, "count": len(items), "defer": False}


async def _page_promotions(db, state, *, token, client_id) -> dict:
    """Promotion PARTICIPATION evidence. GET /v1/actions enumerates the seller's actions; only
    POST /v1/actions/products proves a product is ACTIVELY in a given action_id (participation_status
    ='active'). The action_id is the sole identity — a title is never used. Candidates are NOT fetched
    here: an eligible candidate is not a participant and would be a false observation.

    Positive-only: this writer NEVER records not_participating (its store-catalog reconciliation is out
    of scope). Absence of an active row is not proof of non-participation. Append-only means a partial
    or failed pass leaves every prior run's proven participation untouched. coverage_complete is set
    true ONLY after every action has been walked to its last page with no failure."""
    now = datetime.utcnow()
    cur = json.loads(state.cursor) if state.cursor else {}
    run_id = cur.get("run_id") or str(uuid.uuid4())

    # First page of the pass: enumerate the actions and pin their order for the rest of the pass.
    if "aids" not in cur:
        actions = await ozon_client.list_actions(token=token, client_id=client_id)
        aids: list[str] = []
        for a in actions:
            if isinstance(a, dict):
                aid = _s(a.get("id"))
                if aid and aid not in aids:
                    aids.append(aid)
        state.coverage_complete = False
        if not aids:
            state.coverage_complete = True     # an empty action set IS a complete, clean coverage
            state.cursor = None
            return {"done": True, "count": 0, "defer": False}
        state.cursor = json.dumps({"run_id": run_id, "aids": aids, "ai": 0, "lid": 0})
        return {"done": False, "count": 0, "defer": False}

    aids = cur.get("aids") or []
    ai = cur.get("ai", 0)
    lid = cur.get("lid", 0)
    if ai >= len(aids):
        state.coverage_complete = True         # every action fully traversed with no failure
        state.cursor = None
        return {"done": True, "count": 0, "defer": False}

    action_id = aids[ai]
    # The action window (identity is the id, never the title).
    actions = await ozon_client.list_actions(token=token, client_id=client_id)
    meta = next((a for a in actions if isinstance(a, dict) and _s(a.get("id")) == action_id), {})
    vf, vt = _dt(meta.get("date_start")), _dt(meta.get("date_end"))

    page = await ozon_client.action_products(
        token=token, client_id=client_id, action_id=action_id, last_id=lid, limit=_ACTION_PAGE)
    result = page.get("result") if isinstance(page.get("result"), dict) else {}
    products = result.get("products") or []
    index = await _index(db, state.marketplace_account_id)
    count = 0
    for p in products:
        if not isinstance(p, dict):
            continue
        ext = _s(p.get("id"))
        if ext is None:
            continue
        promo_price = _dec(p.get("action_price"))
        missing = []
        if promo_price is None:
            missing.append("action_price")
        missing.append("currency_code")        # actions/products carries no proven currency
        product_id = await _resolve(db, index, ext, _s(p.get("offer_id")))
        await _upsert_observation(
            db, state, run_id=run_id, ext=ext, observation_kind="promotion",
            promotion_key=action_id, product_id=product_id, now=now,
            promotion_id=action_id, promotion_type="ozon_action",
            participation_status="active",      # proven ONLY by /v1/actions/products
            seller_promo_price=promo_price,
            catalog_price=None, buyer_price=None, provider_min_price=None,
            auto_action_enabled=None,
            currency=None, currency_status="unknown",
            expected_seller_revenue=None, seller_revenue_status="unknown",
            commission_base=None, commission_base_status="unknown",
            marketplace_subsidy=None, subsidy_status="unknown",
            provider_valid_from=vf, provider_valid_to=vt,
            provider_dataset="actions", missing_fields=missing)
        count += 1

    next_last = result.get("last_id")
    if next_last not in (None, "", 0) and next_last != lid:
        # a real, advancing cursor token → keep walking THIS action
        state.cursor = json.dumps({"run_id": run_id, "aids": aids, "ai": ai, "lid": next_last})
        return {"done": False, "count": count, "defer": False}
    if next_last is not None and next_last == lid and products:
        # the cursor repeated on a non-empty page → fail closed. Rolling back keeps prior runs intact;
        # coverage_complete stays false and no negative is ever written.
        raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "ozon actions cursor did not advance")
    if next_last is None and len(products) >= _ACTION_PAGE:
        # no cursor token but a full page → offset fallback; guard against a non-advancing offset.
        new_lid = (lid or 0) + len(products)
        if new_lid == lid:
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "ozon actions page did not advance")
        state.cursor = json.dumps({"run_id": run_id, "aids": aids, "ai": ai, "lid": new_lid})
        return {"done": False, "count": count, "defer": False}
    # this action is exhausted → advance to the next action, resetting the intra-action cursor
    state.cursor = json.dumps({"run_id": run_id, "aids": aids, "ai": ai + 1, "lid": 0})
    return {"done": False, "count": count, "defer": False}
