"""Yandex Market API ingestion (PULT-LAUNCH-1.4.5G).

Yandex is the two-axis marketplace: a business CABINET (`businessId`) holds MANY campaign STORES
(`campaignId`). PULT models that exactly — MarketplaceAccount.external_account_id = businessId,
MarketplaceStore.external_store_id = campaignId — and this provider keeps the axes apart:

  * products / cards — POST /businesses/{businessId}/offer-mappings (BUSINESS level). marketSku is
    the Product's authoritative external id when present (a card matched on Market); offerId is the
    seller SKU inside the cabinet; the title is never identity. One Product per Account; one
    ProductPlacement per campaign Store the offer is synced for. The card (ImportedCardContentRow)
    is stored ONCE per business (account-level), never copied per campaign.
  * prices  — GET /campaigns/{campaignId}/offer-prices → ImportedProductRow.price (Decimal; unknown
    = NULL, never 0); campaign-scoped, never touches stock.
  * stocks  — POST /campaigns/{campaignId}/offers/stocks → ImportedProductRow.stock. Sellable buckets
    (AVAILABLE / FIT) summed across the campaign's warehouses ONCE (mutually-exclusive types, never
    double-counted); two campaigns are never summed together; never touches price.
  * orders  — GET /campaigns/{campaignId}/orders → MarketplaceOperation(order). An order is an order
    lifecycle, never a sale and never revenue. campaignId is namespaced into external_operation_id.
  * returns — GET /campaigns/{campaignId}/returns?type=RETURN → MarketplaceOperation(return). Only
    from the official returns endpoint and only type=RETURN (a non-purchase / UNREDEEMED is not a
    return; a return is never inferred from a cancellation). No official refund amount → amount NULL.

`finance` is declared but UNSUPPORTED: Yandex financial data is an ASYNC report whose per-row money
schema is not confirmed against the official docs, and PULT never invents money rows. Its
ApiSyncState is parked at status='unsupported' with a reason and makes zero API calls, rather than
guessing a shape.

Money is Decimal, never float; unknown = NULL; no buyer PII; no raw payload. Nothing here enters a
user total until the source policy (1.4.5H). Gated behind API_DATA_SYNC_ENABLED=false.
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
from models.marketplace_account import MarketplaceAccount
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_price_observation import MarketplacePriceObservation, PROMO_KEY_SENTINEL
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation, MarketplacePromotionStoreEvidence)
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from services.account_product_resolver import FOUND, AccountProductIndex
from services.marketplace.errors import ExecutionError
from services.marketplace.yandex_client import yandex_client

MARKETPLACE = "yandex"
# 'finance' is listed so it gets a visible, honest ApiSyncState, but it is UNSUPPORTED (below).
# PULT-LAUNCH-2.5D-Yandex-A adds 'price_observations'; -B3 adds 'promotions' — account-level promo
# participation EVIDENCE. Read-only; reached only through run_api_sync_once (0 calls while OFF).
DATA_TYPES = ("products", "cards", "prices", "stocks", "orders", "returns", "finance",
              "price_observations", "promotions")
UNSUPPORTED = frozenset({"finance"})

_PROMO_OFFERS_PAGE = 500
_MAX_PROMO_PAGES = 10000

# Explicit status normalization (no guessing): provider_status → (participation, auto, attribution).
# A status NOT in this table is unknown — kept verbatim, normalized to 'unknown', coverage_complete=false.
_PROMO_STATUS_NORM = {
    "AUTO":               ("active", True, "account_wide"),
    "PARTIALLY_AUTO":     ("active", True, "exact_stores"),
    "MANUAL":             ("active", False, "unresolved"),
    "NOT_PARTICIPATING":  ("not_participating", None, "unresolved"),
    "RENEWED":            ("active", True, "unresolved"),
    "RENEW_FAILED":       ("unknown", None, "unresolved"),
    "MINIMUM_FOR_PROMOS": ("active", False, "unresolved"),
}


def _promo_schema_error(reason: str) -> ExecutionError:
    """A structural provider-response error (safe reason only — no payload / offerId / campaignId /
    businessId), so a broken page is never mistaken for an empty one."""
    return ExecutionError(ExecutionError.MARKETPLACE_5XX, f"yandex promos schema invalid: {reason}")


def _normalize_promo_status(provider_status: str) -> tuple[str, Optional[bool], str, bool]:
    """(participation_status, auto_participation, attribution_status, known). Unknown/future → unknown."""
    norm = _PROMO_STATUS_NORM.get(provider_status)
    if norm is None:
        return "unknown", None, "unresolved", False
    return norm[0], norm[1], norm[2], True
UNSUPPORTED_REASON = "yandex_finance_async_report_schema_unconfirmed"

# Sellable stock buckets. FBS/Express warehouses report AVAILABLE; FBY warehouses report FIT. The
# buckets are mutually exclusive per (warehouse, offer), so summing these two never double-counts.
_SELLABLE_STOCK = {"AVAILABLE", "FIT"}


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
    """Yandex order/return dates are 'DD-MM-YYYY HH:MM:SS'; ISO shows up for updatedAt."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.replace("Z", "").split(".")[0], fmt)
        except ValueError:
            continue
    return None


# ISO 4217 canonicalization for the ONLY legacy code Yandex uses: `RUR` is Yandex's historical code
# for the Russian ruble; the current ISO 4217 code is `RUB` (what WB/Ozon observations store). This is
# an EXPLICIT, tested mapping — never a hidden substitution inside the writer. Any other well-formed
# 3-letter code passes through verbatim; an absent/malformed code is unknown (None).
_ISO_4217_ALIAS = {"RUR": "RUB"}


def _iso_currency(value) -> tuple[Optional[str], str]:
    """(currency, status). Proven only when the provider gives a well-formed 3-letter code; `RUR` is
    canonicalized to the current ISO 4217 `RUB`. Absent/malformed → (None, 'unknown'); never a default."""
    s = _s(value)
    if not (s and len(s) == 3 and s.isalpha()):
        return None, "unknown"
    return _ISO_4217_ALIAS.get(s.upper(), s.upper()), "proven"


# ── Sync gating (Yandex-specific; the scheduler asks the provider, never branches itself) ─────────
def sync_gate(account: Optional[MarketplaceAccount], store: MarketplaceStore) -> tuple[bool, Optional[str]]:
    """A Yandex store syncs only when BOTH ids are known: the cabinet's businessId
    (Account.external_account_id) and this store's campaignId (Store.external_store_id). A keyless
    store the seller has not mapped to a campaign is parked, never guessed onto a campaign."""
    if account is None or not (account.external_account_id or "").strip():
        return False, "unmapped_business"
    if not (store.external_store_id or "").strip():
        return False, "unmapped_campaign"
    return True, None


async def _index(db, account_id: str) -> AccountProductIndex:
    products = (await db.execute(
        select(Product).where(Product.marketplace_account_id == account_id))).scalars().all()
    return AccountProductIndex(products, account_id)


def _owner(state) -> str:
    return getattr(state, "_owner_user_id", "") or ""


async def _business_id(db, state) -> Optional[str]:
    acc = (await db.execute(select(MarketplaceAccount).where(
        MarketplaceAccount.id == state.marketplace_account_id))).scalars().first()
    return _s(acc.external_account_id) if acc else None


async def _campaign_id(db, state) -> Optional[str]:
    store = (await db.execute(select(MarketplaceStore).where(
        MarketplaceStore.id == state.marketplace_store_id))).scalars().first()
    return _s(store.external_store_id) if store else None


async def _resolve(db, index: AccountProductIndex, ext: Optional[str], sku: Optional[str]) -> Optional[str]:
    if ext is None and sku is None:
        return None
    res = index.resolve(external_product_id=ext, sku=sku)
    return res.product_id if res.status == FOUND else None


async def _placed_in_store(db, store_id: str, product_id: str) -> bool:
    """True iff the product has a ProductPlacement in THIS campaign store — the store-scoped guard a
    catalog observation needs before it may claim a resolved product_id."""
    row = (await db.execute(select(ProductPlacement.id).where(
        ProductPlacement.marketplace_store_id == store_id,
        ProductPlacement.product_id == product_id))).first()
    return row is not None


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


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def fetch_and_persist_page(db, state, token: str, client_id: Optional[str] = None) -> dict:
    """One step for state.data_type. `client_id` is part of the uniform provider signature but is
    NEVER used by Yandex — the real context (businessId, campaignId) is read explicitly from the
    Account/Store bound to the state, so the business/campaign distinction is never conflated."""
    if state.data_type in UNSUPPORTED:
        # Defensive: the scheduler parks these without calling us; never guess a shape.
        return {"done": True, "count": 0, "defer": False, "unsupported": True}
    if state.data_type in ("products", "cards"):
        return await _page_offer_mappings(db, state, token, kind=state.data_type)
    if state.data_type == "prices":
        return await _page_prices(db, state, token)
    if state.data_type == "price_observations":
        return await _page_price_observations(db, state, token)
    if state.data_type == "promotions":
        return await _page_promotions(db, state, token)
    if state.data_type == "stocks":
        return await _page_stocks(db, state, token)
    if state.data_type == "orders":
        return await _page_orders(db, state, token)
    if state.data_type == "returns":
        return await _page_returns(db, state, token)
    raise ValueError(f"unsupported Yandex data_type: {state.data_type}")


async def _page_offer_mappings(db, state, token, *, kind: str) -> dict:
    """BUSINESS-level catalog. kind='products' writes Product+Placement(+identity snapshot);
    kind='cards' writes the account-level ImportedCardContentRow ONCE per business."""
    now = datetime.utcnow()
    business_id = await _business_id(db, state)
    if not business_id:
        return {"done": True, "count": 0, "defer": False}
    page_token = json.loads(state.cursor).get("page_token") if state.cursor else None
    data = await yandex_client.offer_mappings(token=token, business_id=business_id, page_token=page_token)
    result = (data or {}).get("result") or {}
    mappings = result.get("offerMappings") or []
    index = await _index(db, state.marketplace_account_id)
    count = 0
    for m in mappings:
        if not isinstance(m, dict):
            continue
        offer = m.get("offer") if isinstance(m.get("offer"), dict) else {}
        mapping = m.get("mapping") if isinstance(m.get("mapping"), dict) else {}
        offer_id = _s(offer.get("offerId"))
        market_sku = _s(mapping.get("marketSku"))
        if offer_id is None and market_sku is None:
            continue
        # marketSku (global business-level id) is identity when present; else the seller offerId.
        ext = market_sku or offer_id
        title = _s(offer.get("name"))
        product_id = await _resolve(db, index, market_sku, offer_id)
        if product_id is None:
            product = Product(id=str(uuid.uuid4()), user_id=_owner(state),
                              name=title or offer_id or ext, marketplace=MARKETPLACE, sku=offer_id,
                              marketplace_account_id=state.marketplace_account_id,
                              external_product_id=market_sku)
            db.add(product); await db.flush(); index.add(product)
            product_id = product.id

        if kind == "products":
            # Availability/identity for THIS campaign store — a Placement + a store snapshot row.
            await _ensure_placement(db, product_id, state.marketplace_account_id, state.marketplace_store_id)
            row = await _snapshot_row(db, state, ext)
            row.product_id = product_id
            row.link_status = "linked"
            row.fetched_at = now
        else:  # cards — account-level, ONE row per business, not copied per campaign
            card = (await db.execute(select(ImportedCardContentRow).where(
                ImportedCardContentRow.marketplace_account_id == state.marketplace_account_id,
                ImportedCardContentRow.source == "api",
                ImportedCardContentRow.external_row_id == ext))).scalars().first()
            if card is None:
                card = ImportedCardContentRow(
                    id=str(uuid.uuid4()), import_id=state.id, user_id=_owner(state),
                    marketplace=MARKETPLACE, source="api", external_row_id=ext,
                    marketplace_account_id=state.marketplace_account_id)
                db.add(card)
            card.sku = offer_id
            card.title = title
            card.description = _s(offer.get("description"))
            category = offer.get("category")
            card.category = _s(category if not isinstance(category, dict) else category.get("name"))
            card.brand = _s(offer.get("vendor"))
            card.product_id = product_id
            card.link_status = "linked"
            card.fetched_at = now
        count += 1

    next_token = ((result.get("paging") or {}).get("nextPageToken")) or None
    done = not mappings or not next_token
    state.cursor = None if done else json.dumps({"page_token": next_token})
    return {"done": done, "count": count, "defer": False}


async def _page_prices(db, state, token) -> dict:
    now = datetime.utcnow()
    campaign_id = await _campaign_id(db, state)
    if not campaign_id:
        return {"done": True, "count": 0, "defer": False}
    page_token = json.loads(state.cursor).get("page_token") if state.cursor else None
    data = await yandex_client.campaign_prices(token=token, campaign_id=campaign_id, page_token=page_token)
    result = (data or {}).get("result") or {}
    offers = result.get("offers") or []
    index = await _index(db, state.marketplace_account_id)
    for o in offers:
        if not isinstance(o, dict):
            continue
        offer_id = _s(o.get("id") or o.get("offerId"))
        market_sku = _s(o.get("marketSku"))
        ext = offer_id or market_sku
        if ext is None:
            continue
        price_obj = o.get("price") if isinstance(o.get("price"), dict) else {}
        row = await _snapshot_row(db, state, ext)
        row.price = _dec(price_obj.get("value"))          # unknown = NULL; stock untouched
        row.product_id = await _resolve(db, index, market_sku, offer_id)
        row.link_status = "linked" if row.product_id else "unassigned"
        row.fetched_at = now
    next_token = ((result.get("paging") or {}).get("nextPageToken")) or None
    done = not offers or not next_token
    state.cursor = None if done else json.dumps({"page_token": next_token})
    return {"done": done, "count": len(offers), "defer": False}


async def _page_stocks(db, state, token) -> dict:
    now = datetime.utcnow()
    campaign_id = await _campaign_id(db, state)
    if not campaign_id:
        return {"done": True, "count": 0, "defer": False}
    page_token = json.loads(state.cursor).get("page_token") if state.cursor else None
    data = await yandex_client.campaign_stocks(token=token, campaign_id=campaign_id, page_token=page_token)
    result = (data or {}).get("result") or {}
    warehouses = result.get("warehouses") or []
    index = await _index(db, state.marketplace_account_id)
    # Aggregate across THIS campaign's warehouses per offer — one campaign's stock, summed once.
    totals: dict[str, Optional[int]] = {}
    for wh in warehouses:
        if not isinstance(wh, dict):
            continue
        for o in (wh.get("offers") or []):
            if not isinstance(o, dict):
                continue
            offer_id = _s(o.get("offerId"))
            if offer_id is None:
                continue
            for s in (o.get("stocks") or []):
                if isinstance(s, dict) and _s(s.get("type")) in _SELLABLE_STOCK and s.get("count") is not None:
                    totals[offer_id] = (totals.get(offer_id) or 0) + (_int(s.get("count")) or 0)
                elif offer_id not in totals:
                    totals[offer_id] = None
    for offer_id, total in totals.items():
        row = await _snapshot_row(db, state, offer_id)
        row.stock = total                                  # price untouched
        row.product_id = await _resolve(db, index, None, offer_id)
        row.link_status = "linked" if row.product_id else "unassigned"
        row.fetched_at = now
    next_token = ((result.get("paging") or {}).get("nextPageToken")) or None
    done = not warehouses or not next_token
    state.cursor = None if done else json.dumps({"page_token": next_token})
    return {"done": done, "count": len(totals), "defer": False}


async def _page_orders(db, state, token) -> dict:
    now = datetime.utcnow()
    campaign_id = await _campaign_id(db, state)
    if not campaign_id:
        return {"done": True, "count": 0, "defer": False}
    cur = json.loads(state.cursor) if state.cursor else {}
    # Yandex caps the orders window at 30 days; walk from a fixed floor to now in one running window.
    from_date = cur.get("from") or "01-01-2023"
    to_date = now.strftime("%d-%m-%Y")
    page_token = cur.get("page_token")
    data = await yandex_client.campaign_orders(
        token=token, campaign_id=campaign_id, from_date=from_date, to_date=to_date, page_token=page_token)
    orders = (data or {}).get("orders") or []
    index = await _index(db, state.marketplace_account_id)
    for o in orders:
        if not isinstance(o, dict):
            continue
        oid = _s(o.get("id"))
        if oid is None:
            continue
        items = o.get("items") or []
        product_id = None
        if len(items) == 1 and isinstance(items[0], dict):
            product_id = await _resolve(db, index, None, _s(items[0].get("offerId")))
        qty = sum((_int(it.get("count")) or 0) for it in items if isinstance(it, dict)) or None
        # An order is an order lifecycle, never a sale. campaignId namespaces the id across stores.
        # itemsTotal is the buyer-facing order cost, kept as a signed Decimal amount (never PII).
        await _upsert_operation(
            db, state, external_operation_id=f"order:{campaign_id}:{oid}", operation_type="order",
            provider_dataset="orders", provider_code=_s(o.get("status")), product_id=product_id,
            occurred_at=_dt(o.get("creationDate")), status=_s(o.get("substatus") or o.get("status")),
            quantity=qty, amount=_dec(o.get("itemsTotal") or o.get("buyerItemsTotal")),
            currency="RUR", now=now)
    next_token = ((data.get("paging") or {}).get("nextPageToken")) or None
    done = not orders or not next_token
    state.cursor = json.dumps({"from": from_date, "page_token": next_token}) if not done \
        else json.dumps({"from": from_date})
    return {"done": done, "count": len(orders), "defer": False}


async def _page_returns(db, state, token) -> dict:
    now = datetime.utcnow()
    campaign_id = await _campaign_id(db, state)
    if not campaign_id:
        return {"done": True, "count": 0, "defer": False}
    page_token = json.loads(state.cursor).get("page_token") if state.cursor else None
    data = await yandex_client.campaign_returns(token=token, campaign_id=campaign_id, page_token=page_token)
    result = (data or {}).get("result") or {}
    returns = result.get("returns") or []
    index = await _index(db, state.marketplace_account_id)
    for r in returns:
        if not isinstance(r, dict):
            continue
        rid = _s(r.get("id"))
        if rid is None:
            continue
        items = r.get("items") or []
        product_id = None
        if len(items) == 1 and isinstance(items[0], dict):
            product_id = await _resolve(db, index, None, _s(items[0].get("offerId")))
        qty = sum((_int(it.get("count")) or 0) for it in items if isinstance(it, dict)) or None
        # Only official type=RETURN reaches here (the client filters UNREDEEMED out). No official
        # refund amount is exposed → amount stays NULL, never a guessed money value.
        await _upsert_operation(
            db, state, external_operation_id=f"return:{campaign_id}:{rid}", operation_type="return",
            provider_dataset="returns", provider_code="return", parent=_s(r.get("orderId")), product_id=product_id,
            occurred_at=_dt(r.get("creationDate")), quantity=qty, amount=None, currency=None, now=now)
    next_token = ((result.get("paging") or {}).get("nextPageToken")) or None
    done = not returns or not next_token
    state.cursor = None if done else json.dumps({"page_token": next_token})
    return {"done": done, "count": len(returns), "defer": False}


# ── Price OBSERVATIONS (PULT-LAUNCH-2.5D-Yandex-A) ───────────────────────────────
# Append-only, immutable CAMPAIGN price evidence into MarketplacePriceObservation. Idempotency is the
# table's run-uniqueness key (store, external product, KIND, promotion, source, ingest_run_id): the
# ingest_run_id is minted once per full sync pass (kept in the cursor) so re-processing a page UPSERTs
# the same row, while the next pass writes fresh rows. Store-scoped: the campaignId queried is THIS
# store's external_store_id, and every row carries state.marketplace_store_id — a campaign's price is
# never copied to another campaign. Money is Decimal, never float; unknown = NULL, never 0. Revenue,
# subsidy, commission-base, promo and club prices are NEVER inferred here.


async def _upsert_observation(db, state, *, run_id: str, ext: str, product_id: Optional[str],
                              now: datetime, **fields) -> None:
    row = (await db.execute(select(MarketplacePriceObservation).where(
        MarketplacePriceObservation.marketplace_store_id == state.marketplace_store_id,
        MarketplacePriceObservation.external_product_id == ext,
        MarketplacePriceObservation.observation_kind == "catalog",
        MarketplacePriceObservation.promotion_key == PROMO_KEY_SENTINEL,
        MarketplacePriceObservation.source == "api",
        MarketplacePriceObservation.ingest_run_id == run_id))).scalars().first()
    if row is None:
        row = MarketplacePriceObservation(
            id=str(uuid.uuid4()), ingest_run_id=run_id,
            marketplace_account_id=state.marketplace_account_id,
            marketplace_store_id=state.marketplace_store_id,
            external_product_id=ext, observation_kind="catalog",
            promotion_key=PROMO_KEY_SENTINEL, source="api")
        db.add(row)
    # A resolved row MUST carry a product_id (placement FK + resolution CHECK); an unassigned row never
    # borrows another product — product_id stays NULL (never the first fuzzy match, never by title).
    row.product_id = product_id
    row.resolution_status = "resolved" if product_id else "unassigned"
    row.fetched_at = now
    for key, value in fields.items():
        setattr(row, key, value)


# Hard fail-closed ceiling on total pages in ONE pass (far past any real catalog). A cursor that never
# terminates would otherwise walk forever across scheduler ticks.
_MAX_PRICE_OBS_PAGES = 10000


def _schema_error(reason: str) -> ExecutionError:
    """A structural provider-response error. The reason is SAFE (no payload, no offerId, no ids) — it
    describes only the shape that was violated, so a broken page is never mistaken for an empty one."""
    return ExecutionError(ExecutionError.MARKETPLACE_5XX, f"yandex offer-prices schema invalid: {reason}")


async def _page_price_observations(db, state, token) -> dict:
    """Yandex-A campaign price EVIDENCE from GET /v2/campaigns/{campaignId}/offer-prices →
    observation_kind='catalog'. price.value→buyer_price, price.discountBase→catalog_price,
    price.currencyId→currency (proven only, RUR→RUB via _iso_currency, never a default). All other
    money and every flag stay NULL/unknown. Absent values → NULL + named in missing_fields.

    A REAL empty page (result present, offers=[], no nextPageToken) ends the pass successfully. A
    BROKEN page (missing container, offers not an array, paging/nextPageToken of the wrong type) is a
    schema error that fails closed — never turned into an empty catalog. A row whose identity cannot be
    determined is skipped (skipped_rows_count++), and coverage_complete is claimed ONLY when the whole
    pass parsed cleanly with zero skipped rows. Positive rows from clean pages are kept (honest
    partial), matching the existing per-page-commit ingest model; a schema error rolls back its page."""
    now = datetime.utcnow()
    campaign_id = await _campaign_id(db, state)
    if not campaign_id:
        # Store not yet mapped to a campaignId — nothing to observe, never guessed onto a campaign.
        state.cursor = None
        return {"done": True, "count": 0, "defer": False}
    cur = json.loads(state.cursor) if state.cursor else {}
    fresh = "run_id" not in cur
    run_id = cur.get("run_id") or str(uuid.uuid4())
    page_token = cur.get("page_token")
    seen = list(cur.get("seen") or [])
    if fresh:
        # A new pass starts honest: coverage not yet proven, no rows skipped yet.
        state.coverage_complete = False
        state.skipped_rows_count = 0

    data = await yandex_client.campaign_prices(token=token, campaign_id=campaign_id, page_token=page_token)

    # ── Distinguish a VALID empty page from a BROKEN one (schema error → fail closed). ──
    if not isinstance(data, dict):
        raise _schema_error("response is not an object")
    result = data.get("result")
    if not isinstance(result, dict):
        raise _schema_error("missing or non-object result container")
    offers = result.get("offers")
    if not isinstance(offers, list):
        raise _schema_error("offers missing or not an array")   # empty [] is valid; absent/other is not
    paging = result.get("paging")
    if paging is None:
        paging = {}
    if not isinstance(paging, dict):
        raise _schema_error("paging is not an object")
    next_token = paging.get("nextPageToken")
    if next_token is not None and (not isinstance(next_token, str) or not next_token.strip()):
        raise _schema_error("nextPageToken is not a non-empty string")

    index = await _index(db, state.marketplace_account_id)
    for o in offers:
        if not isinstance(o, dict):
            state.skipped_rows_count = (state.skipped_rows_count or 0) + 1   # corrupt row, no identity
            continue
        offer_id = _s(o.get("id") or o.get("offerId"))
        market_sku = _s(o.get("marketSku"))
        ext = offer_id or market_sku
        if ext is None:
            state.skipped_rows_count = (state.skipped_rows_count or 0) + 1   # identity undeterminable
            continue
        price_obj = o.get("price") if isinstance(o.get("price"), dict) else {}
        buyer_price = _dec(price_obj.get("value"))          # current price → buyer_price
        catalog_price = _dec(price_obj.get("discountBase"))  # зачёркнутая → catalog_price
        currency, currency_status = _iso_currency(price_obj.get("currencyId"))
        missing = []
        if buyer_price is None:
            missing.append("value")
        if catalog_price is None:
            missing.append("discountBase")
        if currency is None:
            missing.append("currencyId")
        # Resolution is store-scoped: a Product resolved at the account level counts ONLY if it is
        # actually placed in THIS campaign store. A product placed in a DIFFERENT campaign of the same
        # cabinet must stay unassigned here — never attributed to a store where it has no placement
        # (which the placement composite FK would reject anyway).
        product_id = await _resolve(db, index, market_sku, offer_id)
        if product_id is not None and not await _placed_in_store(db, state.marketplace_store_id, product_id):
            product_id = None
        await _upsert_observation(
            db, state, run_id=run_id, ext=ext, product_id=product_id, now=now,
            promotion_id=None, promotion_type=None, participation_status=None,
            catalog_price=catalog_price, buyer_price=buyer_price,
            seller_promo_price=None, club_buyer_price=None, provider_min_price=None,
            auto_action_enabled=None,
            currency=currency, currency_status=currency_status,
            # never inferred from a buyer-facing price:
            expected_seller_revenue=None, seller_revenue_status="unknown",
            commission_base=None, commission_base_status="unknown",
            marketplace_subsidy=None, subsidy_status="unknown",
            provider_dataset="prices", missing_fields=missing)

    # ── Token cycle detection: reject A→A, A→B→A and any longer loop, and a bounded page ceiling. ──
    if next_token is not None and (next_token == page_token or next_token in seen):
        raise _schema_error("pageToken cycle detected")
    if page_token is not None:
        seen.append(page_token)     # remember every token actually walked in THIS pass
    if len(seen) >= _MAX_PRICE_OBS_PAGES:
        raise _schema_error("page limit exceeded")

    done = next_token is None       # the token is the sole authority for end-of-list
    if done:
        # A pass is complete ONLY when every page parsed cleanly with zero skipped rows.
        state.coverage_complete = (state.skipped_rows_count or 0) == 0
        state.cursor = None
    else:
        state.cursor = json.dumps({"run_id": run_id, "page_token": next_token, "seen": seen})
    return {"done": done, "count": len(offers), "defer": False}


# ── Account-level promotion EVIDENCE (PULT-LAUNCH-2.5D-Yandex-B3) ─────────────────
# Business-level: a promo participation status belongs to the cabinet (businessId), not one campaign
# store. The parent (MarketplacePromotionObservation) carries the verbatim provider_status + a
# normalized triple; the child (MarketplacePromotionStoreEvidence) carries the PROVEN campaignIds of a
# PARTIALLY_AUTO row (mapped to a store of this cabinet or kept unmapped). AUTO/MANUAL are account_wide/
# unresolved — never fanned out to a store. The "PARTIALLY_AUTO ⇒ ≥1 child" rule is a TRANSACTIONAL
# WRITER invariant (parent + all children written in one page transaction, or none). Money is Decimal.
#
# NOTE: promotions is business-level but the scheduler serves it per campaign store; each store's pass
# is an independent account-level observation (own run_id/fetched_at). Redundant across stores of one
# cabinet (an account-level schedule would remove that) but always correct: idempotent within a run
# (run-uniqueness key), append-only across runs, children resolved against ALL of the cabinet's stores.


async def _account_stores(db, account_id: str) -> dict:
    """{external_store_id: MarketplaceStore} for ACTIVE, mapped stores of this cabinet. An archived
    store (or an unmapped campaign) is never a mapped target — its campaignId is kept as unmapped
    evidence instead."""
    rows = (await db.execute(select(MarketplaceStore).where(
        MarketplaceStore.marketplace_account_id == account_id,
        MarketplaceStore.status == "active"))).scalars().all()
    out: dict = {}
    for st in rows:
        ext = _s(st.external_store_id)
        if ext is not None:
            out.setdefault(ext, st)
    return out


async def _upsert_promo_parent(db, state, *, run_id, offer_id, promo_id, product_id, now,
                               **fields) -> MarketplacePromotionObservation:
    row = (await db.execute(select(MarketplacePromotionObservation).where(
        MarketplacePromotionObservation.marketplace_account_id == state.marketplace_account_id,
        MarketplacePromotionObservation.external_product_id == offer_id,
        MarketplacePromotionObservation.promotion_id == promo_id,
        MarketplacePromotionObservation.source == "api",
        MarketplacePromotionObservation.ingest_run_id == run_id))).scalars().first()
    if row is None:
        row = MarketplacePromotionObservation(
            id=str(uuid.uuid4()), ingest_run_id=run_id,
            marketplace_account_id=state.marketplace_account_id, marketplace=MARKETPLACE,
            external_product_id=offer_id, promotion_id=promo_id,
            promotion_type="yandex_promo", source="api", provider_dataset="promos")
        db.add(row)
    row.product_id = product_id
    row.resolution_status = "resolved" if product_id else "unassigned"
    row.fetched_at = now
    for key, value in fields.items():
        setattr(row, key, value)
    await db.flush()   # materialize row.id for children
    return row


async def _upsert_promo_child(db, *, parent_id, account_id, external_store_id, store_id, now) -> None:
    existing = (await db.execute(select(MarketplacePromotionStoreEvidence).where(
        MarketplacePromotionStoreEvidence.promotion_observation_id == parent_id,
        MarketplacePromotionStoreEvidence.external_store_id == external_store_id))).scalars().first()
    if existing is not None:
        existing.marketplace_store_id = store_id
        existing.mapping_status = "mapped" if store_id else "unmapped"
        return
    db.add(MarketplacePromotionStoreEvidence(
        id=str(uuid.uuid4()), promotion_observation_id=parent_id, marketplace_account_id=account_id,
        external_store_id=external_store_id, marketplace_store_id=store_id,
        mapping_status="mapped" if store_id else "unmapped", created_at=now))


async def _page_promotions(db, state, token) -> dict:
    """Yandex-B3 account-level promotion evidence from POST /v2/businesses/{businessId}/promos +
    /promos/offers. Parent per (offer, promo); child per campaignId ONLY for PARTIALLY_AUTO. A broken
    page fails closed; a real empty end is a clean pass; a row-level contract violation (unknown status,
    empty/too-long status, PARTIALLY_AUTO without campaignIds, no identity) is skipped
    (skipped_rows_count++) and marks the pass not-complete; positive parents from clean rows are kept."""
    now = datetime.utcnow()
    business_id = await _business_id(db, state)
    if not business_id:
        state.cursor = None
        return {"done": True, "count": 0, "defer": False}
    cur = json.loads(state.cursor) if state.cursor else {}
    fresh = "run_id" not in cur
    run_id = cur.get("run_id") or str(uuid.uuid4())
    if fresh:
        state.coverage_complete = False
        state.skipped_rows_count = 0

    if "pids" not in cur:
        proms = await yandex_client.list_promos(token=token, business_id=business_id)
        if not isinstance(proms, list):
            raise _promo_schema_error("promotions is not an array")
        pids: list[str] = []
        for p in proms:
            if isinstance(p, dict):
                pid = _s(p.get("id"))
                if pid and pid not in pids:
                    pids.append(pid)
        state.coverage_complete = False
        if not pids:
            state.coverage_complete = True     # no promos = clean, complete coverage
            state.cursor = None
            return {"done": True, "count": 0, "defer": False}
        state.cursor = json.dumps({"run_id": run_id, "pids": pids, "pidx": 0, "ptok": None,
                                   "seen": [], "wpx": -1, "cov": True})
        return {"done": False, "count": 0, "defer": False}

    pids = cur.get("pids") or []
    pidx = cur.get("pidx", 0)
    ptok = cur.get("ptok")
    seen = list(cur.get("seen") or [])
    wpx = cur.get("wpx", -1)
    vf, vt = cur.get("vf"), cur.get("vt")
    cov = cur.get("cov", True)
    if pidx >= len(pids):
        state.coverage_complete = bool(cov) and (state.skipped_rows_count or 0) == 0
        state.cursor = None
        return {"done": True, "count": 0, "defer": False}

    promo_id = pids[pidx]
    if wpx != pidx:      # the promo window (id is identity; a title is never used)
        proms = await yandex_client.list_promos(token=token, business_id=business_id)
        meta = next((p for p in proms if isinstance(p, dict) and _s(p.get("id")) == promo_id), {})
        period = meta.get("period") if isinstance(meta.get("period"), dict) else {}
        vf, vt = _s(period.get("dateTimeFrom")), _s(period.get("dateTimeTo"))
        wpx = pidx

    data = await yandex_client.list_promo_offers(
        token=token, business_id=business_id, promo_id=promo_id, page_token=ptok, limit=_PROMO_OFFERS_PAGE)
    # ── VALID empty page vs BROKEN page (schema error → fail closed) ──
    if not isinstance(data, dict):
        raise _promo_schema_error("response is not an object")
    container = data.get("result") if isinstance(data.get("result"), dict) else data.get("data")
    if not isinstance(container, dict):
        raise _promo_schema_error("missing or non-object result/data container")
    offers = container.get("offers")
    if not isinstance(offers, list):
        raise _promo_schema_error("offers missing or not an array")
    paging = data.get("paging") if data.get("paging") is not None else container.get("paging")
    if paging is None:
        paging = {}
    if not isinstance(paging, dict):
        raise _promo_schema_error("paging is not an object")
    next_token = paging.get("nextPageToken")
    if next_token is not None and (not isinstance(next_token, str) or not next_token.strip()):
        raise _promo_schema_error("nextPageToken is not a non-empty string")

    index = await _index(db, state.marketplace_account_id)
    stores = await _account_stores(db, state.marketplace_account_id)
    count = 0
    for o in offers:
        if not isinstance(o, dict):
            state.skipped_rows_count = (state.skipped_rows_count or 0) + 1
            cov = False
            continue
        offer_id = _s(o.get("offerId"))
        provider_status = _s(o.get("status"))
        # identity + portable status guard (empty / whitespace-padded stripped by _s / too long)
        if offer_id is None or provider_status is None or len(provider_status) > 64:
            state.skipped_rows_count = (state.skipped_rows_count or 0) + 1
            cov = False
            continue
        participation, auto, attribution, known = _normalize_promo_status(provider_status)
        if participation == "unknown":
            # unknown/future status AND documented-but-non-actionable RENEW_FAILED both normalize to
            # 'unknown' → the pass is not a complete, trustworthy coverage of participation.
            cov = False

        disc = o.get("params") if isinstance(o.get("params"), dict) else {}
        disc = disc.get("discountParams") if isinstance(disc.get("discountParams"), dict) else {}
        campaign_ids: list[str] = []
        if attribution == "exact_stores":                 # PARTIALLY_AUTO
            apd = o.get("autoParticipatingDetails") if isinstance(o.get("autoParticipatingDetails"), dict) else {}
            raw = apd.get("campaignIds") if isinstance(apd.get("campaignIds"), list) else []
            for c in raw:
                cs = _s(c)
                if cs and cs not in campaign_ids:
                    campaign_ids.append(cs)               # dedupe
            if not campaign_ids:
                # PARTIALLY_AUTO MUST carry campaignIds — provider-contract violation for THIS offer:
                # no parent, no child; the pass is not complete.
                state.skipped_rows_count = (state.skipped_rows_count or 0) + 1
                cov = False
                continue

        pre = promo = mx = None
        currency, currency_status = None, "unknown"
        missing: list[str] = []
        if participation == "active":
            pre = _dec(disc.get("price"))          # price → pre_promo_price
            promo = _dec(disc.get("promoPrice"))   # promoPrice → promo_buyer_price
            mx = _dec(disc.get("maxPromoPrice"))   # maxPromoPrice → promo_max_price (порог, NOT a min)
            if pre is None:
                missing.append("price")
            if promo is None:
                missing.append("promoPrice")
            if mx is None:
                missing.append("maxPromoPrice")
            if pre is not None or promo is not None or mx is not None:
                # Official contract for promo offers: prices are "указываются в рублях" → RUB is proven
                # by documentation for THIS dataset only (never a silent default elsewhere).
                currency, currency_status = "RUB", "proven"

        product_id = await _resolve(db, index, None, offer_id)   # offerId is the seller SKU
        parent = await _upsert_promo_parent(
            db, state, run_id=run_id, offer_id=offer_id, promo_id=promo_id, product_id=product_id, now=now,
            provider_status=provider_status, participation_status=participation,
            auto_participation=auto, attribution_status=attribution,
            pre_promo_price=pre, promo_buyer_price=promo, promo_max_price=mx,
            currency=currency, currency_status=currency_status,
            promotion_start_at=_dt(vf), promotion_end_at=_dt(vt), missing_fields=missing)
        for cid in campaign_ids:
            store = stores.get(cid)
            await _upsert_promo_child(db, parent_id=parent.id, account_id=state.marketplace_account_id,
                                      external_store_id=cid, store_id=(store.id if store else None), now=now)
        count += 1

    # ── token cycle + page ceiling ──
    if next_token is not None and (next_token == ptok or next_token in seen):
        raise _promo_schema_error("pageToken cycle detected")
    if ptok is not None:
        seen.append(ptok)
    if len(seen) >= _MAX_PROMO_PAGES:
        raise _promo_schema_error("page limit exceeded")

    if next_token is not None:                             # more pages for THIS promo
        state.cursor = json.dumps({"run_id": run_id, "pids": pids, "pidx": pidx, "ptok": next_token,
                                   "seen": seen, "wpx": wpx, "vf": vf, "vt": vt, "cov": cov})
        return {"done": False, "count": count, "defer": False}
    # this promo exhausted → next promo, reset intra-promo cursor
    state.cursor = json.dumps({"run_id": run_id, "pids": pids, "pidx": pidx + 1, "ptok": None,
                               "seen": seen, "wpx": wpx, "vf": vf, "vt": vt, "cov": cov})
    return {"done": False, "count": count, "defer": False}
