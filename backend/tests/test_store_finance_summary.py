"""PULT-LAUNCH-1.4.5I-QA2 — GET /api/marketplace-stores/{id}/finance-summary.

Store-level resolved money total. Reuses store_financial_totals (no new calc). Source/completeness/
conflict describe the REVENUE resolution (API vs CSV); net_profit is null (never 0) when an API store
lacks cost of goods; conflict carries the two candidate revenue values, never a sum. Owner-scoped
(foreign/missing → same 404). Flag off ⇒ pure CSV.
"""
import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
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
from models.store_data_source_policy import StoreDataSourcePolicy
from models.user import User
from models.workspace import Workspace
from routers.store_catalog import store_finance_summary

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
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace=marketplace,
                             store_key="primary", label="S", source="manual", status="active")
    db.add(store)
    await db.commit()
    return await db.get(User, uid), acc, store


async def _csv(db, user, acc, store, *, revenue, net=0.0, day="2026-07-10"):
    db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace="wb",
                              date=day, sku="A", revenue=revenue, net_profit=net, source="csv",
                              marketplace_account_id=acc.id, marketplace_store_id=store.id, link_status="linked"))
    await db.commit()


async def _api_op(db, acc, store, *, amount, day="2026-07-10"):
    db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                                marketplace_store_id=store.id, marketplace="wildberries", source="api",
                                external_operation_id=str(uuid.uuid4()), operation_type="sale",
                                provider_dataset="finance", amount=Decimal(amount),
                                occurred_at=datetime.strptime(day, "%Y-%m-%d"), fetched_at=datetime.utcnow()))
    await db.commit()


async def _fin_state(db, acc, store):
    db.add(ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                        marketplace_account_id=acc.id, marketplace_store_id=store.id, data_type="finance",
                        status="synced", coverage_complete=True, skipped_rows_count=0,
                        covered_from="2026-07-01", covered_to="2026-07-31", last_success_at=datetime.utcnow()))
    await db.commit()


async def _policy(db, store, pref):
    db.add(StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id,
                                 metric_type="revenue", preference=pref))
    await db.commit()


# 1. CSV summary
def test_csv_summary():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0, net=30.0))
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.revenue == 100.0 and out.net_profit == 30.0 and out.source == "csv"
    assert out.completeness == "complete" and out.conflict is False and out.conflict_candidates is None


# 2. API summary
def test_api_summary(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "api"))
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.revenue == 250.0 and out.source == "api"


# 3. Revenue conflict + real candidates
def test_conflict_candidates(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0))
    _run(_api_op(db, acc, store, amount="130"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "auto"))
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.conflict is True and out.source == "csv" and out.revenue == 100.0
    assert out.conflict_candidates is not None
    assert out.conflict_candidates.api == 130.0 and out.conflict_candidates.csv == 100.0


# 4. Incomplete without cogs → net_profit null
def test_incomplete_null_profit(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "api"))
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.net_profit is None and out.completeness == "incomplete" and "cogs" in out.missing_fields


# 5. missing_fields is honest (cogs named; nothing fabricated)
def test_missing_fields_honest(flag_on):
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0))
    _run(_api_op(db, acc, store, amount="250"))
    _run(_fin_state(db, acc, store))
    _run(_policy(db, store, "api"))
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.missing_fields == ["cogs"]   # only what is actually missing — no invented ad_spend row


# 6. Flag OFF → CSV, no API mixing (api rows present but ignored)
def test_flag_off_csv():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0, net=30.0))
    _run(_api_op(db, acc, store, amount="250"))
    _run(_policy(db, store, "api"))    # even with an explicit api policy…
    out = _run(store_finance_summary(store.id, db=db, user=user))
    assert out.revenue == 100.0 and out.net_profit == 30.0 and out.source == "csv" and out.conflict is False


# 7. Foreign / missing store → same 404
def test_foreign_and_missing_404():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    other_user, _a, other_store = _run(_seed(db))
    with pytest.raises(HTTPException) as foreign:
        _run(store_finance_summary(other_store.id, db=db, user=user))
    with pytest.raises(HTTPException) as missing:
        _run(store_finance_summary("does-not-exist", db=db, user=user))
    assert foreign.value.status_code == 404 and missing.value.status_code == 404


# 8. Existing user-level finance summary endpoint unaffected
def test_existing_finance_summary_unaffected():
    from services.finance_aggregator import summary_by_product
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_csv(db, user, acc, store, revenue=100.0, net=30.0))
    items, totals = _run(summary_by_product(user.id, db))
    assert totals["revenue"] == 100.0 and totals["net_profit"] == 30.0


# 9. single alembic head
def test_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rob1a2b3c4d01"]
