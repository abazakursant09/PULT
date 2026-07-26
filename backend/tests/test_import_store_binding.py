"""PULT-LAUNCH-1.4.2 — ImportRecord / upload / preview / confirm bound to a MarketplaceStore.

Row-write is unchanged in this slice (1.4.3); here we prove the store binding, the trust
boundary (marketplace/account read from the DB, never the client), and the confirm status
machine (atomic pending→processing, no double write, no confirmed-on-error).
"""
import asyncio
import io
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
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
from services.marketplace.identity_normalize import to_parser_code

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


async def _seed_store(db, uid, *, marketplace="wildberries", status="active", store_key="primary"):
    ws = str(uuid.uuid4()); acc = str(uuid.uuid4()); store = str(uuid.uuid4())
    db.add(Workspace(id=ws, owner_user_id=uid))
    db.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace=marketplace,
                              identity_status="unverified", label="Кабинет"))
    db.add(MarketplaceStore(id=store, marketplace_account_id=acc, marketplace=marketplace,
                            store_key=store_key, label="Магазин", source="manual", status=status))
    await db.commit()
    return SimpleNamespace(ws=ws, account_id=acc, store_id=store)


_FINANCE = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")


def _upload(c, store_id, extra=None):
    data = {"import_type": "finance"}
    if store_id is not None:
        data["marketplace_store_id"] = store_id
    if extra:
        data.update(extra)
    return c.post("/api/import/upload",
                  files={"file": ("finance.csv", io.BytesIO(_FINANCE), "text/csv")}, data=data)


# 1. Upload without store_id -> 422
def test_upload_without_store_rejected():
    db = _run(_new_db()); uid = str(uuid.uuid4()); _run(_seed_store(db, uid))
    r = _upload(_client(db, uid), None)
    assert r.status_code == 422


# 2. Foreign store -> 404 (caller is a real seller with their own workspace)
def test_foreign_store_404():
    db = _run(_new_db()); uid = str(uuid.uuid4()); other = str(uuid.uuid4())
    _run(_seed_store(db, uid))                        # caller's own cabinet/store
    foreign = _run(_seed_store(db, other))           # store owned by someone else
    r = _upload(_client(db, uid), foreign.store_id)
    assert r.status_code == 404


# 3. Archived store -> 409
def test_archived_store_rejected():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    s = _run(_seed_store(db, uid, status="archived"))
    r = _upload(_client(db, uid), s.store_id)
    assert r.status_code == 409


# 4 & 8. account/store/source stored from the DB
def test_import_record_stores_account_store_source():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    r = _upload(_client(db, uid), s.store_id)
    assert r.status_code == 200
    body = r.json()
    assert body["marketplace_account_id"] == s.account_id
    assert body["marketplace_store_id"] == s.store_id
    rec = _run(db.get(ImportRecord, body["import_id"]))
    assert rec.marketplace_account_id == s.account_id
    assert rec.marketplace_store_id == s.store_id
    assert rec.source == "csv"


# 5. Marketplace comes from the Store, not the client form
def test_marketplace_taken_from_store_not_form():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid, marketplace="wildberries"))
    r = _upload(_client(db, uid), s.store_id, extra={"marketplace": "ozon"})   # lie in the form
    assert r.status_code == 200
    assert r.json()["marketplace"] == "wildberries"                            # DB wins
    rec = _run(db.get(ImportRecord, r.json()["import_id"]))
    assert rec.marketplace == "wildberries"


# 6 & 7. parser-code boundary adapter
def test_parser_code_adapter():
    assert to_parser_code("wildberries") == "wb"
    assert to_parser_code("ozon") == "ozon"
    assert to_parser_code("yandex") == "ym"
    assert to_parser_code("wb") == "wb"            # legacy short normalizes then maps
    assert to_parser_code("megamarket") is None    # unknown -> reject, never guessed


# 9. Confirm ignores a store swap in the body
def test_confirm_ignores_body_store_swap():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    iid = _upload(_client(db, uid), s.store_id).json()["import_id"]
    r = _client(db, uid).post(f"/api/import/{iid}/confirm",
                              json={"mode": "new", "marketplace_store_id": "HACKED", "account_id": "HACKED"})
    assert r.status_code == 200
    rec = _run(db.get(ImportRecord, iid))
    assert rec.marketplace_store_id == s.store_id       # unchanged
    assert rec.status == "confirmed"


# 10. Foreign ImportRecord not accessible
def test_foreign_import_record_404():
    db = _run(_new_db()); uid = str(uuid.uuid4()); other = str(uuid.uuid4())
    s = _run(_seed_store(db, other))
    iid = _upload(_client(db, other), s.store_id).json()["import_id"]
    r = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r.status_code == 404


# 11. Legacy record without store_id cannot be confirmed
def test_legacy_record_without_store_refused():
    db = _run(_new_db()); uid = str(uuid.uuid4()); _run(_seed_store(db, uid))
    rid = str(uuid.uuid4())
    async def _mk():
        db.add(ImportRecord(id=rid, user_id=uid, filename="x.csv", file_hash="h",
                            marketplace="wb", import_type="finance", status="pending",
                            temp_path=None))
        await db.commit()
    _run(_mk())
    r = _client(db, uid).post(f"/api/import/{rid}/confirm", json={"mode": "new"})
    assert r.status_code == 400


# 12 & 14. A record already 'processing' is refused (concurrent claim already taken)
def test_processing_record_refused():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    iid = _upload(_client(db, uid), s.store_id).json()["import_id"]
    _run(db.execute(update(ImportRecord).where(ImportRecord.id == iid).values(status="processing")))
    _run(db.commit())
    r = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r.status_code == 409


# 13. Repeat confirm after confirmed writes nothing more
def test_repeat_confirm_no_double_write():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    iid = _upload(_client(db, uid), s.store_id).json()["import_id"]
    r1 = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r1.status_code == 200
    n1 = _run(db.execute(select(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))).scalars().all()
    r2 = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r2.status_code == 400
    n2 = _run(db.execute(select(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))).scalars().all()
    assert len(n1) == len(n2)     # no extra rows


# 15. An error during processing leaves status='failed', never 'confirmed'
def test_error_leaves_failed_not_confirmed(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    iid = _upload(_client(db, uid), s.store_id).json()["import_id"]

    async def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(csv_import, "_persist_import_rows", _boom)

    r = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r.status_code == 500
    rec = _run(db.get(ImportRecord, iid))
    assert rec.status == "failed"


# 16. Archiving the store does not delete the ImportRecord
def test_archive_store_keeps_import_record():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_seed_store(db, uid))
    iid = _upload(_client(db, uid), s.store_id).json()["import_id"]
    _run(db.execute(update(MarketplaceStore).where(MarketplaceStore.id == s.store_id).values(status="archived")))
    _run(db.commit())
    assert _run(db.get(ImportRecord, iid)) is not None     # record survives
    # and a confirm now refuses because the store is archived
    r = _client(db, uid).post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert r.status_code == 409


# 17. Alembic upgrade/downgrade/re-upgrade
def test_migration_roundtrip(tmp_path):
    import os
    from alembic.config import Config
    from alembic import command
    dbp = tmp_path / "m.db"
    os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{dbp.as_posix()}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "spl1a2b3c4d01")
        import sqlite3
        cols0 = [r[1] for r in sqlite3.connect(dbp).execute("PRAGMA table_info('import_records')")]
        assert "marketplace_store_id" not in cols0
        command.upgrade(cfg, "head")
        cols1 = [r[1] for r in sqlite3.connect(dbp).execute("PRAGMA table_info('import_records')")]
        assert {"marketplace_account_id", "marketplace_store_id", "source"} <= set(cols1)
        command.downgrade(cfg, "spl1a2b3c4d01")
        cols2 = [r[1] for r in sqlite3.connect(dbp).execute("PRAGMA table_info('import_records')")]
        assert "marketplace_store_id" not in cols2
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("ALEMBIC_DATABASE_URL", None)


# 18. Single alembic head
def test_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert len(ScriptDirectory.from_config(Config("alembic.ini")).get_heads()) == 1
