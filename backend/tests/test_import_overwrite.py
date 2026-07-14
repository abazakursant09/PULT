"""The import UI offered "Перезаписать", but confirm only ever appended, so re-importing the
same report double-counted revenue. confirm now takes mode="overwrite", which drops the rows of
prior confirmed imports of the SAME file (same user + file_hash) before inserting. These tests
prove overwrite does not double totals, touches only the current user, and leaves a second
seller's data intact.
"""
import asyncio
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
import models  # registers tables
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from routers import csv_import

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


_CSV = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")

# The finance dashboard's headline revenue for this file, per import.
_REVENUE_ONE = 1000.0


def _upload_and_confirm(c, mode="new"):
    up = c.post("/api/import/upload",
                files={"file": ("finance.csv", _CSV, "text/csv")},
                data={"marketplace": "wildberries", "import_type": "finance"})
    assert up.status_code == 200, up.text
    import_id = up.json()["import_id"]
    cf = c.post(f"/api/import/{import_id}/confirm", json={"mode": mode})
    assert cf.status_code == 200, cf.text
    return cf.json()


async def _total_revenue(db, uid):
    r = await db.execute(select(func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0))
                         .where(ImportedFinanceRow.user_id == uid))
    return r.scalar_one()


def test_overwrite_same_file_twice_does_not_double_totals():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)

    _upload_and_confirm(c, mode="new")                      # first copy
    assert _run(_total_revenue(db, uid)) == _REVENUE_ONE

    _upload_and_confirm(c, mode="overwrite")               # re-upload, overwrite
    # One dataset, not two: revenue is NOT doubled.
    assert _run(_total_revenue(db, uid)) == _REVENUE_ONE


def test_new_mode_still_appends():
    # "Импортировать как новый" keeps the additive behaviour.
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)

    _upload_and_confirm(c, mode="new")
    _upload_and_confirm(c, mode="new")
    assert _run(_total_revenue(db, uid)) == 2 * _REVENUE_ONE


def test_overwrite_only_touches_current_user():
    db = _run(_new_db())
    uid_a = str(uuid.uuid4())
    uid_b = str(uuid.uuid4())

    # Seller B imports the same file and keeps it.
    _upload_and_confirm(_client(db, uid_b), mode="new")
    assert _run(_total_revenue(db, uid_b)) == _REVENUE_ONE

    # Seller A imports the same file twice with overwrite.
    ca = _client(db, uid_a)
    _upload_and_confirm(ca, mode="new")
    _upload_and_confirm(ca, mode="overwrite")

    # A collapsed to one dataset; B is completely untouched.
    assert _run(_total_revenue(db, uid_a)) == _REVENUE_ONE
    assert _run(_total_revenue(db, uid_b)) == _REVENUE_ONE


def test_overwrite_with_no_prior_import_just_inserts():
    # Overwrite on a first-ever import must not error and must insert normally.
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)
    _upload_and_confirm(c, mode="overwrite")
    assert _run(_total_revenue(db, uid)) == _REVENUE_ONE
