"""SECURITY-2D-2-B (IMP-1) — confirm-time duplicate-CSV guard on REAL PostgreSQL 16.

Proves the aggregate guarantee under true cross-connection concurrency: at most ONE mode='new' confirm of
a given raw-bytes SHA-256 can commit within one tenant scope, so revenue/returns are never double-counted;
overwrite stays a deliberate replace; and a different store / different tenant with the same bytes is NOT
blocked. Uses the real confirm_import with independent AsyncSessions + asyncio.gather over per-record temp
CSV files (fixture bytes only — never a user's file). Skipped locally; runs in postgres-explain CI (0 skip).
"""
import asyncio
import hashlib
import os
import shutil
import tempfile
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import routers.csv_import as csv_import
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.workspace import Workspace

_FINANCE = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")
_HASH = hashlib.sha256(_FINANCE).hexdigest()


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    return (_pg_sync_url() or "").replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    return os.environ.get("PULT_TEST_PG_ALEMBIC_URL") or _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL; runs in postgres-explain CI.")

_SCHEMA_READY = False
_TABLES = ("import_records", "imported_finance_rows", "marketplace_stores",
           "marketplace_accounts", "workspaces", "products", "product_placements")


def _ensure_schema(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import sqlalchemy as sa
    if not _SCHEMA_READY:
        from alembic import command
        import db_migrations as dbm
        eng = sa.create_engine(_pg_sync_url())
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(dbm._alembic_config(), "head")
        eng.dispose()
        _SCHEMA_READY = True
    eng = sa.create_engine(_pg_sync_url())
    with eng.begin() as c:
        c.exec_driver_sql("TRUNCATE %s CASCADE" % ", ".join(_TABLES))
    eng.dispose()


def _session():
    eng = create_async_engine(_pg_async_url())
    return eng, sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _tenant(Session, *, uid, marketplace="wildberries", store_key="primary"):
    ws = str(uuid.uuid4())
    acc = str(uuid.uuid4())
    store = str(uuid.uuid4())
    async with Session() as s:
        s.add(Workspace(id=ws, owner_user_id=uid))
        s.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace=marketplace,
                                 identity_status="unverified", label="Кабинет"))
        s.add(MarketplaceStore(id=store, marketplace_account_id=acc, marketplace=marketplace,
                               store_key=store_key, label="Магазин", source="manual", status="active"))
        await s.commit()
    return SimpleNamespace(ws=ws, account_id=acc, store_id=store)


def _tmp_csv(tmpdir):
    p = os.path.join(tmpdir, f"{uuid.uuid4()}.csv")
    with open(p, "wb") as f:
        f.write(_FINANCE)
    return p


async def _record(Session, *, uid, t, tmpdir, import_type="finance"):
    rid = str(uuid.uuid4())
    async with Session() as s:
        s.add(ImportRecord(
            id=rid, user_id=uid, filename="finance.csv", file_hash=_HASH,
            marketplace="wildberries", import_type=import_type, status="pending",
            temp_path=_tmp_csv(tmpdir), marketplace_account_id=t.account_id,
            marketplace_store_id=t.store_id, source="csv"))
        await s.commit()
    return rid


async def _confirm(Session, rid, uid, mode):
    async with Session() as s:
        return await csv_import.confirm_import(
            rid, BackgroundTasks(), csv_import.ConfirmRequest(mode=mode),
            db=s, user=SimpleNamespace(id=uid))


async def _rows(Session, account_id):
    async with Session() as s:
        return (await s.execute(
            select(func.count()).select_from(ImportedFinanceRow)
            .where(ImportedFinanceRow.marketplace_account_id == account_id))).scalar_one()


async def _revenue(Session, account_id):
    async with Session() as s:
        return (await s.execute(
            select(func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0))
            .where(ImportedFinanceRow.marketplace_account_id == account_id))).scalar_one()


def _classify(results):
    ok = sum(1 for r in results if not isinstance(r, Exception))
    h409 = sum(1 for r in results if isinstance(r, csv_import.HTTPException) and r.status_code == 409)
    h500 = sum(1 for r in results if isinstance(r, csv_import.HTTPException) and r.status_code == 500)
    other = len(results) - ok - h409 - h500
    return ok, h409, h500, other


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. normal new ────────────────────────────────────────────────────────────────

def test_pg_normal_new(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            rid = await _record(S, uid=uid, t=t, tmpdir=tmp)
            r = await _confirm(S, rid, uid, "new")
            assert r.imported_count == 1
            assert await _rows(S, t.account_id) == 1
            assert await _revenue(S, t.account_id) == 1000.0
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 2. sequential same hash (also the lost-ACK contract) ───────────────────────────

def test_pg_sequential_same_hash_second_409_no_double(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            a = await _record(S, uid=uid, t=t, tmpdir=tmp)
            b = await _record(S, uid=uid, t=t, tmpdir=tmp)
            await _confirm(S, a, uid, "new")                        # durable commit
            assert await _rows(S, t.account_id) == 1                # verified via independent session
            with pytest.raises(csv_import.HTTPException) as ei:
                await _confirm(S, b, uid, "new")                    # retry / second import_id
            assert ei.value.status_code == 409
            async with S() as s:
                assert (await s.get(ImportRecord, b)).status == "failed"
            assert await _rows(S, t.account_id) == 1                # no second set
            assert await _revenue(S, t.account_id) == 1000.0        # revenue not doubled
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 3. concurrent[2] same hash ─────────────────────────────────────────────────────

def test_pg_concurrent_two_same_hash_one_confirmed(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            a = await _record(S, uid=uid, t=t, tmpdir=tmp)
            b = await _record(S, uid=uid, t=t, tmpdir=tmp)
            res = await asyncio.gather(_confirm(S, a, uid, "new"), _confirm(S, b, uid, "new"),
                                       return_exceptions=True)
            ok, h409, h500, other = _classify(res)
            assert (ok, h409, h500, other) == (1, 1, 0, 0)
            assert await _rows(S, t.account_id) == 1
            assert await _revenue(S, t.account_id) == 1000.0
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 4. concurrent[10] same hash ────────────────────────────────────────────────────

def test_pg_concurrent_ten_same_hash_one_confirmed(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            ids = [await _record(S, uid=uid, t=t, tmpdir=tmp) for _ in range(10)]
            res = await asyncio.gather(*[_confirm(S, i, uid, "new") for i in ids],
                                       return_exceptions=True)
            ok, h409, h500, other = _classify(res)
            assert ok == 1                                          # exactly one winner
            assert h409 == 9                                        # nine duplicates rejected
            assert h500 == 0 and other == 0                         # no 500 / no unexpected
            assert await _rows(S, t.account_id) == 1                # exactly one file's rows
            assert await _revenue(S, t.account_id) == 1000.0        # aggregate of one file
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 5. different store, same tenant → both confirmed ───────────────────────────────

def test_pg_different_store_same_tenant_both_confirmed(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            ws = str(uuid.uuid4())
            # one workspace, two WB cabinets (keyless → NULL external id, both allowed), one store each
            t = []
            async with S() as s:
                s.add(Workspace(id=ws, owner_user_id=uid))
                for _ in range(2):
                    acc = str(uuid.uuid4())
                    store = str(uuid.uuid4())
                    s.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace="wildberries",
                                             identity_status="unverified", label="Кабинет"))
                    s.add(MarketplaceStore(id=store, marketplace_account_id=acc, marketplace="wildberries",
                                           store_key="primary", label="Магазин", source="manual",
                                           status="active"))
                    t.append(SimpleNamespace(account_id=acc, store_id=store))
                await s.commit()
            a = await _record(S, uid=uid, t=t[0], tmpdir=tmp)
            b = await _record(S, uid=uid, t=t[1], tmpdir=tmp)
            res = await asyncio.gather(_confirm(S, a, uid, "new"), _confirm(S, b, uid, "new"),
                                       return_exceptions=True)
            ok, h409, _, other = _classify(res)
            assert ok == 2 and h409 == 0 and other == 0             # different scope → NOT a duplicate
            assert await _rows(S, t[0].account_id) == 1
            assert await _rows(S, t[1].account_id) == 1
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 6. cross-tenant → not blocked / not leaked ─────────────────────────────────────

def test_pg_cross_tenant_not_blocked(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid1 = str(uuid.uuid4())
            uid2 = str(uuid.uuid4())
            t1 = await _tenant(S, uid=uid1)
            t2 = await _tenant(S, uid=uid2)
            a = await _record(S, uid=uid1, t=t1, tmpdir=tmp)
            b = await _record(S, uid=uid2, t=t2, tmpdir=tmp)
            await _confirm(S, a, uid1, "new")
            r = await _confirm(S, b, uid2, "new")                   # same bytes, other tenant → allowed
            assert r.imported_count == 1
            assert await _rows(S, t1.account_id) == 1
            assert await _rows(S, t2.account_id) == 1
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 7. new vs overwrite, same scope → serialized, one final set ────────────────────

def test_pg_new_vs_overwrite_serialized_one_set(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            a = await _record(S, uid=uid, t=t, tmpdir=tmp)
            b = await _record(S, uid=uid, t=t, tmpdir=tmp)
            res = await asyncio.gather(_confirm(S, a, uid, "new"), _confirm(S, b, uid, "overwrite"),
                                       return_exceptions=True)
            ok, h409, h500, other = _classify(res)
            assert h500 == 0 and other == 0                         # no crash / no unexpected
            assert ok >= 1                                          # overwrite always succeeds
            assert await _rows(S, t.account_id) == 1                # never two copies in the scope
            assert await _revenue(S, t.account_id) == 1000.0
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())


# ── 8. overwrite vs overwrite → one final set ──────────────────────────────────────

def test_pg_overwrite_vs_overwrite_one_set(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()
    tmp = tempfile.mkdtemp()

    async def go():
        try:
            uid = str(uuid.uuid4())
            t = await _tenant(S, uid=uid)
            a = await _record(S, uid=uid, t=t, tmpdir=tmp)
            b = await _record(S, uid=uid, t=t, tmpdir=tmp)
            res = await asyncio.gather(_confirm(S, a, uid, "overwrite"), _confirm(S, b, uid, "overwrite"),
                                       return_exceptions=True)
            ok, h409, h500, other = _classify(res)
            assert h500 == 0 and other == 0
            assert ok == 2                                          # both are deliberate replaces
            assert await _rows(S, t.account_id) == 1                # one final replacement set, no mix
            assert await _revenue(S, t.account_id) == 1000.0
        finally:
            await eng.dispose()
            shutil.rmtree(tmp, ignore_errors=True)
    _run(go())
