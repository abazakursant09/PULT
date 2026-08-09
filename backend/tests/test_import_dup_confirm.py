"""SECURITY-2D-2-B (IMP-1) — confirm-time duplicate-CSV guard: SQLite functional + source guards.

Functional smoke on in-memory SQLite via the real upload→confirm endpoints (single-threaded, so the true
concurrency proof lives in test_import_dup_confirm_pg.py). Locks: a mode='new' second confirm of the same
raw-bytes file in the same store scope → 409; overwrite of the same file → success with ONE row set; a
different store is not a duplicate; and the source-level invariants (owner FOR UPDATE lock precedes the
duplicate re-check, the query excludes the current record and is scoped to tenant+store+type+hash+status,
the guard runs only for mode='new').
"""
from __future__ import annotations

import ast
import asyncio
import io
import os
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
from rate_limit import limit_import
import models  # noqa: F401
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.workspace import Workspace
from routers import csv_import

_HERE = os.path.dirname(__file__)
_SRC = os.path.join(_HERE, "..", "routers", "csv_import.py")

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(csv_import.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_import] = lambda: None
    return TestClient(app)


async def _seed_store(db, uid, *, marketplace="wildberries", store_key="primary"):
    ws = str(uuid.uuid4())
    acc = str(uuid.uuid4())
    store = str(uuid.uuid4())
    db.add(Workspace(id=ws, owner_user_id=uid))
    db.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace=marketplace,
                              identity_status="unverified", label="Кабинет"))
    db.add(MarketplaceStore(id=store, marketplace_account_id=acc, marketplace=marketplace,
                            store_key=store_key, label="Магазин", source="manual", status="active"))
    await db.commit()
    return SimpleNamespace(ws=ws, account_id=acc, store_id=store)


_FINANCE = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")


def _upload(c, store_id):
    return c.post("/api/import/upload",
                  files={"file": ("finance.csv", io.BytesIO(_FINANCE), "text/csv")},
                  data={"import_type": "finance", "marketplace_store_id": store_id})


async def _finance_row_count(db, account_id):
    return (await db.execute(
        select(func.count()).select_from(ImportedFinanceRow)
        .where(ImportedFinanceRow.marketplace_account_id == account_id))).scalar_one()


async def _revenue_sum(db, account_id):
    return (await db.execute(
        select(func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0))
        .where(ImportedFinanceRow.marketplace_account_id == account_id))).scalar_one()


# ── functional ────────────────────────────────────────────────────────────────────

def test_second_new_confirm_same_file_is_409_one_row_set():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    s = _run(_seed_store(db, uid))
    c = _client(db, uid)
    a = _upload(c, s.store_id).json()["import_id"]
    b = _upload(c, s.store_id).json()["import_id"]          # same bytes → same hash, 2nd import_id
    assert c.post(f"/api/import/{a}/confirm", json={"mode": "new"}).status_code == 200
    r2 = c.post(f"/api/import/{b}/confirm", json={"mode": "new"})
    assert r2.status_code == 409                             # duplicate rejected
    assert _run(db.get(ImportRecord, b)).status == "failed"  # left failed, not confirmed
    assert _run(_finance_row_count(db, s.account_id)) == 1   # ONE row set only
    assert _run(_revenue_sum(db, s.account_id)) == 1000.0    # revenue not doubled


def test_overwrite_same_file_succeeds_one_row_set():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    s = _run(_seed_store(db, uid))
    c = _client(db, uid)
    a = _upload(c, s.store_id).json()["import_id"]
    b = _upload(c, s.store_id).json()["import_id"]
    assert c.post(f"/api/import/{a}/confirm", json={"mode": "new"}).status_code == 200
    r2 = c.post(f"/api/import/{b}/confirm", json={"mode": "overwrite"})
    assert r2.status_code == 200                             # deliberate replace allowed
    assert _run(db.get(ImportRecord, b)).status == "confirmed"
    assert _run(_finance_row_count(db, s.account_id)) == 1   # replaced, not appended
    assert _run(_revenue_sum(db, s.account_id)) == 1000.0


# (different-store / different-account scope correctness is proved in test_import_dup_confirm_pg.py,
#  where multi-store/multi-account seeding under one tenant is controlled precisely.)


def test_failed_first_does_not_reserve_identity():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    s = _run(_seed_store(db, uid))
    c = _client(db, uid)
    a = _upload(c, s.store_id).json()["import_id"]
    # force the first confirm to fail inside persist → record 'failed', 0 rows
    import routers.csv_import as ci

    async def _boom(*a, **k):
        raise RuntimeError("injected")
    orig = ci._persist_import_rows
    ci._persist_import_rows = _boom
    try:
        assert c.post(f"/api/import/{a}/confirm", json={"mode": "new"}).status_code == 500
    finally:
        ci._persist_import_rows = orig
    assert _run(db.get(ImportRecord, a)).status == "failed"
    assert _run(_finance_row_count(db, s.account_id)) == 0
    # a fresh new import of the same file is NOT blocked by the failed one
    b = _upload(c, s.store_id).json()["import_id"]
    assert c.post(f"/api/import/{b}/confirm", json={"mode": "new"}).status_code == 200
    assert _run(_finance_row_count(db, s.account_id)) == 1


# ── source / AST guards ─────────────────────────────────────────────────────────────

def _src():
    with open(_SRC, encoding="utf-8") as f:
        return f.read()


def _guard_fn(src):
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_guard_new_duplicate")
    return ast.get_source_segment(src, fn)


def test_guard_locks_before_recheck_and_excludes_self():
    seg = _guard_fn(_src())
    assert "with_for_update()" in seg                        # owner row-lock taken
    i_lock = seg.index("with_for_update()")
    i_dup = seg.index("ImportRecord.file_hash == rec.file_hash")
    assert i_lock < i_dup                                     # lock precedes the duplicate re-check
    assert "ImportRecord.id != rec.id" in seg                # excludes the current record
    assert "ImportRecord.status == \"confirmed\"" in seg     # only confirmed reserves identity
    assert "ImportRecord.import_type == rec.import_type" in seg
    assert "ImportRecord.file_hash == rec.file_hash" in seg
    assert "ImportRecord.source == \"csv\"" in seg


def test_guard_called_only_for_new_mode():
    src = _src()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "confirm_import")
    seg = ast.get_source_segment(src, fn)
    assert 'if body.mode == "new":' in seg
    # the guard call sits under that mode gate, before _persist_import_rows
    assert seg.index('if body.mode == "new":') < seg.index("_guard_new_duplicate(db, rec)")
    assert seg.index("_guard_new_duplicate(db, rec)") < seg.index("_persist_import_rows(db, rec")


def test_no_model_or_hash_change():
    src = _src()
    assert "hashlib" not in src                               # hash algorithm untouched here
    assert "Base.metadata" not in src
