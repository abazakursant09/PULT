"""PULT-LAUNCH-1.4.5H2 — the source policy is WIRED into real calculations, not decorative.

Each test seeds real rows, flips a store's policy, and proves the actual aggregator / producer /
Learning result changes — plus the flag-off regression (numbers byte-identical) and a purity guard
that the policy-aware readers are single-source.
"""
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta
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
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.store_data_source_policy import StoreDataSourcePolicy
from models.user import User
from models.workspace import Workspace
import services.finance_aggregator as fa
from services.marketplace.finance_metric_reader import read_net_profit
from services.marketplace.metric_reader import MetricUnavailable
from services.source_policy.snapshot_source import resolved_snapshot
from services.price_erosion.diagnosis_source import build_price_series

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


async def _csv_fin(db, user, acc, store, *, revenue, net=0.0, commission=0.0, logistics=0.0,
                   qty=0, day="2026-07-10", sku="A", mp="wb", store_id="use"):
    db.add(ImportedFinanceRow(
        id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace=mp, date=day, sku=sku,
        revenue=revenue, net_profit=net, commission=commission, logistics=logistics, quantity=qty,
        source="csv", marketplace_account_id=acc.id,
        marketplace_store_id=(store.id if store_id == "use" else store_id), link_status="linked"))
    await db.commit()


async def _api_op(db, acc, store, *, dataset, op_type, amount=None, qty=None, day="2026-07-10",
                  marketplace="wildberries"):
    db.add(MarketplaceOperation(
        id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace_store_id=store.id,
        marketplace=marketplace, source="api", external_operation_id=str(uuid.uuid4()),
        operation_type=op_type, provider_dataset=dataset,
        amount=(Decimal(amount) if amount is not None else None), quantity=qty,
        occurred_at=datetime.strptime(day, "%Y-%m-%d"), fetched_at=datetime.utcnow()))
    await db.commit()


async def _fin_state(db, acc, store, data_type="finance", *, complete=True,
                     covered=("2026-07-01", "2026-07-31"), last=None):
    db.add(ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                        marketplace_account_id=acc.id, marketplace_store_id=store.id,
                        data_type=data_type, status="synced", coverage_complete=complete,
                        skipped_rows_count=0, covered_from=covered[0], covered_to=covered[1],
                        last_success_at=(last or datetime.utcnow())))
    await db.commit()


async def _policy(db, store, metric, pref):
    db.add(StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id,
                                 metric_type=metric, preference=pref))
    await db.commit()


# ── 1: finance period total actually changes with policy ─────────────────────────
def test_agg_period_switches_to_api(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250"))
    _run(_fin_state(db, acc, store))
    # csv preference → CSV number
    _run(_policy(db, store, "revenue", "csv"))
    p_csv = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p_csv.revenue == 100.0 and p_csv.source == "csv"
    # flip to api → API number, profit suppressed (no cost of goods)
    pol = _run(db.execute(select(StoreDataSourcePolicy).where(
        StoreDataSourcePolicy.metric_type == "revenue"))).scalars().first()
    pol.preference = "api"; _run(db.commit())
    p_api = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p_api.revenue == 250.0 and p_api.source == "api"
    assert p_api.completeness == "incomplete" and "cogs" in p_api.missing_fields


# 21/2: two stores resolved individually then summed
def test_two_stores_individually_resolved(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    store2 = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="wildberries",
                              store_key=str(uuid.uuid4()) if False else "primary", external_store_id=None,
                              label="S2", source="api", status="active")
    # second store must be a different cabinet for WB (one primary per cabinet); make a new account
    acc2 = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()), marketplace="wildberries",
                              identity_status="verified", external_account_id=f"E-{uuid.uuid4()}")
    db.add(acc2); _run(db.commit())
    store2.marketplace_account_id = acc2.id; db.add(store2); _run(db.commit())
    _run(_csv_fin(db, user, acc, store, revenue=100.0))
    _run(_csv_fin(db, user, acc2, store2, revenue=40.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))     # store1 api
    _run(_policy(db, store2, "revenue", "csv"))    # store2 csv
    p = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p.revenue == 290.0    # 250 (api store1) + 40 (csv store2)


# 23: legacy NULL-store CSV preserved
def test_legacy_null_store_csv(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=0.0, store_id=None))   # legacy, no store
    _run(_csv_fin(db, user, acc, store, revenue=0.0, store_id=None, day="2026-07-11"))
    # give the null-store rows real revenue
    for r in _run(db.execute(select(ImportedFinanceRow))).scalars().all():
        r.revenue = 55.0
    _run(db.commit())
    p = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p.revenue == 110.0 and p.source == "csv"


# 11: conflict propagates to the period
def test_conflict_propagates(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="130"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "auto"))
    p = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p.conflict is True and p.revenue == 100.0 and p.source == "csv"   # safe CSV on conflict


# 30: flag OFF — api rows ignored, csv result identical
def test_flag_off_ignores_api():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))    # even with an explicit api policy…
    # flag is OFF (default) → API never enters
    p = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p.revenue == 100.0 and p.source == "csv" and p.profit == 30.0


# 3: store_financial_totals is resolver-wired
def test_store_totals_switches(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "revenue", "api"))
    out = _run(fa.store_financial_totals(store.id, db))
    assert out["revenue"] == 250.0 and out["source"] == "api"
    assert out["net_profit"] is None and out["completeness"] == "incomplete"


def test_store_totals_flag_off_csv():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250"))
    out = _run(fa.store_financial_totals(store.id, db))
    assert out["revenue"] == 100.0 and out["net_profit"] == 30.0 and out["source"] == "csv"


# 6: Learning net_profit is CSV-source; an API-only store yields honest absence
def test_learning_csv_only(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    # only API money exists for this sku; no CSV finance row
    _run(_api_op(db, acc, store, dataset="finance", op_type="sale", amount="250", day=datetime.utcnow().date().isoformat()))
    res = _run(read_net_profit(db=db, user_id=user.id, marketplace="wb", entity_id="A"))
    assert isinstance(res, MetricUnavailable)   # never fabricates net_profit from API revenue


def test_learning_reads_csv_value():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    today = datetime.utcnow().date().isoformat()
    _run(_csv_fin(db, user, acc, store, revenue=100.0, net=30.0, day=today, sku="A"))
    res = _run(read_net_profit(db=db, user_id=user.id, marketplace="wb", entity_id="A"))
    assert not isinstance(res, MetricUnavailable) and res.value == 30.0


# 4/25: snapshot producer resolves to the API series when policy=api
def test_snapshot_resolves_api_series(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    # csv + api price snapshots for one sku, one store
    db.add(ImportedProductRow(id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace="wildberries",
                              sku="A", price=100.0, source="csv", marketplace_account_id=acc.id,
                              marketplace_store_id=store.id))
    db.add(ImportedProductRow(id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace="wildberries",
                              sku="A", price=80.0, source="api", marketplace_account_id=acc.id,
                              marketplace_store_id=store.id))
    _run(db.commit())
    _run(_fin_state(db, acc, store, data_type="prices"))
    _run(_policy(db, store, "price", "api"))
    src, store_id = _run(resolved_snapshot(db, user.id, "wildberries", "A", "price"))
    assert src == "api" and store_id == store.id
    series = _run(build_price_series(db, user.id, "wildberries", "A", source=src, store_id=store_id))
    assert series == [80.0]   # API series only, never interleaved with the CSV 100.0


def test_snapshot_flag_off_csv():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    src, store_id = _run(resolved_snapshot(db, user.id, "wildberries", "A", "price"))
    assert src == "csv" and store_id is None


# ── Purity guard (functional + source inspection) ────────────────────────────────
def test_purity_finance_aggregator_single_source():
    # functional: a stray api ImportedFinanceRow is never summed by the period aggregator (flag off)
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv_fin(db, user, acc, store, revenue=100.0))
    db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace="wb",
                              date="2026-07-10", sku="A", revenue=999.0, source="api",
                              marketplace_account_id=acc.id, marketplace_store_id=store.id,
                              link_status="linked"))
    _run(db.commit())
    p = _run(fa._agg_period(user.id, "2026-07-01", "2026-07-31", db))
    assert p.revenue == 100.0   # api row in the finance table is never blended in


def test_purity_source_inspection():
    # every ImportedFinanceRow breakdown query in the aggregator carries a source filter or routes
    # through the resolver; the money reader always filters provider_dataset.
    fa_src = inspect.getsource(fa)
    assert fa_src.count('ImportedFinanceRow.source == "csv"') >= 5
    from services.source_policy import money_reader as mr
    mr_src = inspect.getsource(mr)
    assert "provider_dataset.in_" in mr_src
    from services.source_policy import store_totals as st
    assert 'ImportedFinanceRow.source == "csv"' in inspect.getsource(st)


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["pev1a2b3c4d01"]
