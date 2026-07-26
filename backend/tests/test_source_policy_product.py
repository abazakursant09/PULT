"""PULT-LAUNCH-1.4.5H3 — product-level API attribution.

Per-product API money is used only for operations unambiguously tied to a Product and only when
attribution is complete; an unassigned operation is never split across products. WB finance rows can
inherit a Product from their order via an exact external_parent_id match (both arrival orders). The
canonical per-product summary switches source; rankings are marked incomplete when the API is in play.
"""
import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from config import settings
from models.api_sync_state import ApiSyncState
from models.imported_finance import ImportedFinanceRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.store_data_source_policy import StoreDataSourcePolicy
from models.user import User
from models.workspace import Workspace
import services.finance_aggregator as fa
from services.marketplace.ingest import wb as wbmod
from services.source_policy import product_money_reader as pmr
from services.source_policy.parent_link import backfill_children_product, resolve_parent_product
from services.source_policy.product_totals import resolved_products

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", True)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, marketplace="wildberries"):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace=marketplace,
                             identity_status="verified", external_account_id=f"E-{uuid.uuid4()}")
    db.add(acc)
    key = "primary" if marketplace in ("wildberries", "ozon") else str(uuid.uuid4())
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                             marketplace=marketplace, store_key=key,
                             external_store_id=(None if marketplace != "yandex" else "cmp-1"),
                             label="S", source="api", status="active")
    db.add(store)
    await db.commit()
    return await db.get(User, uid), acc, store


def _product(db, user, acc, *, ext="555", sku="A", name="P"):
    p = Product(id=str(uuid.uuid4()), user_id=user.id, name=name, marketplace="wildberries",
                sku=sku, marketplace_account_id=acc.id, external_product_id=ext)
    db.add(p)
    return p


class _State:
    def __init__(self, acc, store):
        self.marketplace_account_id = acc.id
        self.marketplace_store_id = store.id


async def _op(db, acc, store, *, ext_id, op_type, dataset, product_id=None, parent=None,
              amount=None, day="2026-07-10"):
    st = _State(acc, store)
    await wbmod._upsert_operation(
        db, st, external_operation_id=ext_id, operation_type=op_type, provider_dataset=dataset,
        parent=parent, product_id=product_id,
        amount=(Decimal(amount) if amount is not None else None),
        occurred_at=datetime.strptime(day, "%Y-%m-%d"), now=datetime.utcnow())
    await db.commit()


# ── Attribution & parent link (1-7) ──────────────────────────────────────────────
def test_direct_product_and_null(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="rrd1", op_type="sale", dataset="finance", product_id=p.id, amount="100"))
    _run(_op(db, acc, store, ext_id="rrd2", op_type="sale", dataset="finance", product_id=None, amount="50"))
    money = _run(pmr.api_product_money(db, store_id=store.id, product_id=p.id, marketplace="wildberries",
                                       metric_type="revenue", period=None))
    assert money == Decimal("100")   # only the product-attributed op; NULL not attributed


def test_parent_link_order_before_finance():
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="srid1", op_type="order", dataset="orders", product_id=p.id))
    # finance arrives with NO product but names the order via parent=srid → inherits on write
    _run(_op(db, acc, store, ext_id="rrdX", op_type="sale", dataset="finance", parent="srid1", amount="90"))
    op = _run(db.execute(select(MarketplaceOperation).where(
        MarketplaceOperation.external_operation_id == "rrdX"))).scalars().first()
    assert op.product_id == p.id


def test_parent_link_finance_before_order():
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    # finance first, no product, parent=srid (order not yet present) → stays NULL
    _run(_op(db, acc, store, ext_id="rrdY", op_type="sale", dataset="finance", parent="srid2", amount="70"))
    op = _run(db.execute(select(MarketplaceOperation).where(
        MarketplaceOperation.external_operation_id == "rrdY"))).scalars().first()
    assert op.product_id is None
    # order arrives later → backfills the waiting finance row
    _run(_op(db, acc, store, ext_id="srid2", op_type="order", dataset="orders", product_id=p.id))
    op = _run(db.execute(select(MarketplaceOperation).where(
        MarketplaceOperation.external_operation_id == "rrdY"))).scalars().first()
    assert op.product_id == p.id


def test_parent_ambiguous_not_linked():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    p1 = _product(db, user, acc, ext="1", sku="A"); p2 = _product(db, user, acc, ext="2", sku="B")
    _run(db.commit())
    # two ops share the same external id 'dup' with DIFFERENT products → ambiguous parent
    db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace_store_id=store.id,
                                marketplace="wildberries", source="api", external_operation_id="dup",
                                operation_type="order", provider_dataset="orders", product_id=p1.id,
                                fetched_at=datetime.utcnow()))
    db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace_store_id=store.id,
                                marketplace="wildberries", source="api", external_operation_id="dup",
                                operation_type="sale", provider_dataset="sales", product_id=p2.id,
                                fetched_at=datetime.utcnow()))
    _run(db.commit())
    got = _run(resolve_parent_product(db, acc.id, "dup"))
    assert got is None   # ambiguous → never guess


def test_parent_link_cross_account_blocked():
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    user2, acc2, store2 = _run(_seed(db))
    _run(_op(db, acc, store, ext_id="srid9", op_type="order", dataset="orders", product_id=p.id))
    # a different account asking for the same parent id gets nothing
    got = _run(resolve_parent_product(db, acc2.id, "srid9"))
    assert got is None


# ── Completeness (8-12) ──────────────────────────────────────────────────────────
async def _fin_state(db, acc, store, dt="finance", *, complete=True, skipped=0):
    db.add(ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                        marketplace_account_id=acc.id, marketplace_store_id=store.id, data_type=dt,
                        status="synced", coverage_complete=complete, skipped_rows_count=skipped,
                        covered_from="2026-07-01", covered_to="2026-07-31", last_success_at=datetime.utcnow()))
    await db.commit()


def test_attribution_complete(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="100"))
    _run(_fin_state(db, acc, store))
    attr = _run(pmr.attribution_completeness(db, store_id=store.id, marketplace="wildberries",
                                             metric_type="revenue", period=None))
    assert attr.complete is True and attr.unassigned == 0


def test_attribution_one_unassigned(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="100"))
    _run(_op(db, acc, store, ext_id="r2", op_type="sale", dataset="finance", product_id=None, amount="10"))
    _run(_fin_state(db, acc, store))
    attr = _run(pmr.attribution_completeness(db, store_id=store.id, marketplace="wildberries",
                                             metric_type="revenue", period=None))
    assert attr.complete is False and attr.unassigned == 1 and attr.reason == "unassigned_operations"


def test_attribution_skipped_incomplete(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="100"))
    _run(_fin_state(db, acc, store, skipped=1))
    attr = _run(pmr.attribution_completeness(db, store_id=store.id, marketplace="wildberries",
                                             metric_type="revenue", period=None))
    assert attr.complete is False and attr.reason == "coverage_incomplete"


# ── resolved_products & summary (13-16, 23-28, 29, 32-36) ────────────────────────
async def _csv_fin(db, user, acc, store, *, product, revenue, net=0.0, day="2026-07-10"):
    db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace="wb",
                              date=day, sku="A", revenue=revenue, net_profit=net, source="csv",
                              product_id=product.id, marketplace_account_id=acc.id,
                              marketplace_store_id=store.id, link_status="linked"))
    await db.commit()


async def _policy(db, store, metric, pref):
    db.add(StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id,
                                 metric_type=metric, preference=pref))
    await db.commit()


def test_resolved_products_switches_api(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0, net=30.0))
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))
    prods = _run(resolved_products(db, user.id, "2026-07-01", "2026-07-31"))
    pm = prods[p.id]
    assert pm.revenue == 250.0 and pm.source == "api" and pm.net_profit is None
    # API store → profit suppressed (no cogs) → incomplete; attribution itself is fine (single op)
    assert pm.completeness == "incomplete" and "cogs" in pm.missing_fields
    assert "product_attribution" not in pm.missing_fields


def test_resolved_products_incomplete_when_unassigned(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0))
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    _run(_op(db, acc, store, ext_id="r2", op_type="sale", dataset="finance", product_id=None, amount="40"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))
    prods = _run(resolved_products(db, user.id, "2026-07-01", "2026-07-31"))
    assert prods[p.id].completeness == "incomplete" and "product_attribution" in prods[p.id].missing_fields


def test_resolved_products_flag_off():
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0, net=30.0))
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    prods = _run(resolved_products(db, user.id, "2026-07-01", "2026-07-31"))
    assert prods[p.id].revenue == 100.0 and prods[p.id].net_profit == 30.0 and prods[p.id].source == "csv"


def test_summary_by_product_switches(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0, net=30.0))
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))
    items, totals = _run(fa.summary_by_product(user.id, db))
    item = next(i for i in items if i["product_id"] == p.id)
    assert item["total_revenue"] == 250.0 and item["source"] == "api"
    assert item["total_net_profit"] is None and item["completeness"] == "incomplete"


def test_top_products_marked_incomplete(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0, net=30.0))
    _run(_policy(db, store, "revenue", "api"))
    rows = _run(fa._top_products(user.id, "2026-07-01", "2026-07-31", db))
    assert rows and all(r["completeness"] == "incomplete" for r in rows)


def test_dedup_revenue_from_finance_only(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_op(db, acc, store, ext_id="s1", op_type="sale", dataset="sales", product_id=p.id, amount="250"))
    _run(_op(db, acc, store, ext_id="f1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    money = _run(pmr.api_product_money(db, store_id=store.id, product_id=p.id, marketplace="wildberries",
                                       metric_type="revenue", period=None))
    assert money == Decimal("250")   # finance only — not doubled with the sales feed


def test_two_accounts_isolated(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    user2, acc2, store2 = _run(_seed(db)); p2 = _product(db, user2, acc2, ext="999")
    _run(db.commit())
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="100"))
    _run(_op(db, acc2, store2, ext_id="r2", op_type="sale", dataset="finance", product_id=p2.id, amount="500"))
    m1 = _run(pmr.api_product_money(db, store_id=store.id, product_id=p.id, marketplace="wildberries",
                                    metric_type="revenue", period=None))
    assert m1 == Decimal("100")


def test_flag_off_summary_csv():
    db = _run(_new_db()); user, acc, store = _run(_seed(db)); p = _product(db, user, acc); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, product=p, revenue=100.0, net=30.0))
    _run(_op(db, acc, store, ext_id="r1", op_type="sale", dataset="finance", product_id=p.id, amount="250"))
    items, _t = _run(fa.summary_by_product(user.id, db))
    item = next(i for i in items if i["product_id"] == p.id)
    assert item["total_revenue"] == 100.0 and item["total_net_profit"] == 30.0


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["pad1a2b3c4d01"]
