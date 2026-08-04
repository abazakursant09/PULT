"""PULT-LAUNCH-1.4.4 — per-row conflicts, preview counts, safe store/period overwrite, resolution.

TestClient + dependency-override style. One ambiguous SKU no longer aborts the import: the row is
saved as link_status='conflict' while safe rows import; overwrite replaces only the exact
account+store+source+period scope; the seller resolves conflicts via the conflict endpoints.
"""
import asyncio
import io
import os
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
from rate_limit import limit_import
import models  # noqa: F401
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow
from models.imported_card_content import ImportedCardContentRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.workspace import Workspace
from routers import csv_import
from services import finance_aggregator

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


async def _store(db, uid, marketplace="wildberries", store_key="primary"):
    ws = str(uuid.uuid4()); acc = str(uuid.uuid4()); sid = str(uuid.uuid4())
    db.add(Workspace(id=ws, owner_user_id=uid))
    db.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace=marketplace,
                              identity_status="unverified", label="K"))
    db.add(MarketplaceStore(id=sid, marketplace_account_id=acc, marketplace=marketplace,
                            store_key=store_key, label="S", source="manual", status="active"))
    await db.commit()
    return SimpleNamespace(ws=ws, account_id=acc, store_id=sid)


async def _extra_store(db, ws, marketplace="yandex"):
    acc = str(uuid.uuid4()); sid = str(uuid.uuid4())
    db.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace=marketplace,
                              identity_status="unverified", label="K2"))
    db.add(MarketplaceStore(id=sid, marketplace_account_id=acc, marketplace=marketplace,
                            store_key=uuid.uuid4().hex if marketplace == "yandex" else "primary",
                            label="S2", source="manual", status="active"))
    await db.commit()
    return SimpleNamespace(account_id=acc, store_id=sid)


async def _dupe_products(db, uid, account_id, sku, marketplace="wildberries", n=2):
    for _ in range(n):
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name=sku, marketplace=marketplace,
                       sku=sku, marketplace_account_id=account_id))
    await db.commit()


def _finance(sku="ART-1", date="2026-07-01"):
    return ("дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
            f"{date},{sku},Товар,1000,100,50,30,820,3\n").encode("utf-8")


def _products(sku="ART-1"):
    return ("артикул,название,цена,остаток,рейтинг,отзывы\n"
            f"{sku},Товар,100,5,4.5,10\n").encode("utf-8")


def _returns(sku="ART-1"):
    return ("дата,артикул,возвраты,сумма возврата,причина\n"
            f"2026-07-01,{sku},2,200,брак\n").encode("utf-8")


def _card(sku="ART-1"):
    return ("артикул,название,описание,бренд,категория,количество фото\n"
            f"{sku},Товар,Описание,Бренд,Категория,3\n").encode("utf-8")


def _upload(c, store_id, csv_bytes, import_type):
    return c.post("/api/import/upload",
                  files={"file": (f"{import_type}.csv", io.BytesIO(csv_bytes), "text/csv")},
                  data={"marketplace_store_id": store_id, "import_type": import_type})


def _upload_confirm(c, store_id, csv_bytes, import_type, mode="new"):
    up = _upload(c, store_id, csv_bytes, import_type)
    assert up.status_code == 200, up.text
    cf = c.post(f"/api/import/{up.json()['import_id']}/confirm", json={"mode": mode})
    return up, cf


# ── migration: backfill + CHECK ───────────────────────────────────────────────
def test_migration_backfill_and_check(tmp_path):
    from alembic.config import Config
    from alembic import command
    import sqlite3
    dbp = tmp_path / "m.db"
    os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{dbp.as_posix()}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "imp2a2b3c4d01")
        con = sqlite3.connect(dbp)
        con.execute("INSERT INTO imported_finance_rows(id,import_id,user_id,marketplace,revenue,"
                    "commission,logistics,ad_spend,net_profit,quantity,product_id,source) "
                    "VALUES('f1','i','u','wildberries',1,0,0,0,0,0,'p1','csv')")
        con.execute("INSERT INTO imported_finance_rows(id,import_id,user_id,marketplace,revenue,"
                    "commission,logistics,ad_spend,net_profit,quantity,product_id,source) "
                    "VALUES('f2','i','u','wildberries',1,0,0,0,0,0,NULL,'csv')")
        con.commit(); con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(dbp)
        assert con.execute("SELECT link_status FROM imported_finance_rows WHERE id='f1'").fetchone()[0] == "linked"
        assert con.execute("SELECT link_status FROM imported_finance_rows WHERE id='f2'").fetchone()[0] == "unassigned"
        raised = False
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("UPDATE imported_finance_rows SET link_status='bogus' WHERE id='f1'")
            con.commit()
        except sqlite3.IntegrityError:
            raised = True
        con.close()
        assert raised, "CHECK did not reject an unknown link_status"
        command.downgrade(cfg, "imp2a2b3c4d01")
        con = sqlite3.connect(dbp)
        assert "link_status" not in [r[1] for r in con.execute("PRAGMA table_info('imported_finance_rows')")]
        con.close()
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop("ALEMBIC_DATABASE_URL", None)


def test_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rop1a2b3c4d01"]


# ── per-row conflict, safe rows still import, confirmed ────────────────────────
def _conflict_case(import_type, make_csv):
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    c = _client(db, uid)
    up, cf = _upload_confirm(c, s.store_id, make_csv("DUP"), import_type)
    assert cf.status_code == 200                                   # NOT aborted
    body = cf.json()
    assert body["conflicts"] == 1
    rec = _run(db.get(ImportRecord, up.json()["import_id"]))
    assert rec.status == "confirmed"                               # conflicts do not fail the import
    return db, s, uid


def test_conflict_products():
    _conflict_case("products", _products)


def test_conflict_finance():
    _conflict_case("finance", _finance)


def test_conflict_returns():
    _conflict_case("returns", _returns)


def test_conflict_card():
    _conflict_case("card_content", _card)


def test_safe_rows_import_alongside_conflict():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    csv = ("артикул,название,цена,остаток,рейтинг,отзывы\n"
           "DUP,Товар,100,5,4.5,10\nSAFE,Хороший,50,3,4.0,2\n").encode("utf-8")
    c = _client(db, uid)
    _, cf = _upload_confirm(c, s.store_id, csv, "products")
    body = cf.json()
    assert body["conflicts"] == 1 and body["linked"] == 1          # SAFE created, DUP conflict
    rows = _run(db.execute(select(ImportedProductRow))).scalars().all()
    by_sku = {r.sku: r for r in rows}
    assert by_sku["DUP"].link_status == "conflict" and by_sku["DUP"].product_id is None
    assert by_sku["SAFE"].link_status == "linked" and by_sku["SAFE"].product_id is not None


# ── preview ───────────────────────────────────────────────────────────────────
def test_preview_writes_nothing_and_counts():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    csv = ("артикул,название,цена,остаток,рейтинг,отзывы\n"
           "DUP,Товар,100,5,4.5,10\nNEW,Новый,50,3,4.0,2\n").encode("utf-8")
    c = _client(db, uid)
    r = _upload(c, s.store_id, csv, "products")
    assert r.status_code == 200
    body = r.json()
    assert body["conflicts"] == 1 and body["new_products"] == 1
    # preview created nothing
    assert _run(db.execute(select(func.count()).select_from(ImportedProductRow))).scalar() == 0
    assert _run(db.execute(select(func.count()).select_from(ProductPlacement))).scalar() == 0
    assert _run(db.execute(select(func.count()).select_from(Product).where(
        Product.sku == "NEW"))).scalar() == 0


# ── duplicate detection is store-scoped ───────────────────────────────────────
def test_duplicate_same_store_but_not_other_store():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid, "yandex", uuid.uuid4().hex))
    other = _run(_extra_store(db, s.ws, "yandex"))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("A"), "finance")
    # same file, same store → duplicate flagged
    dup = _upload(c, s.store_id, _finance("A"), "finance")
    assert dup.json()["duplicate_import_id"] is not None
    # same file, DIFFERENT store → not a duplicate
    nodup = _upload(c, other.store_id, _finance("A"), "finance")
    assert nodup.json()["duplicate_import_id"] is None


# ── safe overwrite scope ──────────────────────────────────────────────────────
def test_finance_overwrite_leaves_other_store():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid, "yandex", uuid.uuid4().hex))
    other = _run(_extra_store(db, s.ws, "yandex"))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("A"), "finance")
    _upload_confirm(c, other.store_id, _finance("A"), "finance")   # other store, same file/period
    _, cf = _upload_confirm(c, s.store_id, _finance("A"), "finance", mode="overwrite")
    assert cf.json()["replaced"] == 1                              # replaced only store s's row
    a = _run(finance_aggregator.store_financial_totals(s.store_id, db))
    b = _run(finance_aggregator.store_financial_totals(other.store_id, db))
    assert a["revenue"] == 1000.0 and b["revenue"] == 1000.0       # other store intact


def test_products_overwrite_keeps_product_and_placement():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _products("P1"), "products")
    prod_before = _run(db.execute(select(func.count()).select_from(Product))).scalar()
    pl_before = _run(db.execute(select(func.count()).select_from(ProductPlacement))).scalar()
    _upload_confirm(c, s.store_id, _products("P1"), "products", mode="overwrite")
    # metric rows replaced, but Product and Placement are NOT deleted
    assert _run(db.execute(select(func.count()).select_from(Product))).scalar() == prod_before
    assert _run(db.execute(select(func.count()).select_from(ProductPlacement))).scalar() == pl_before


def test_overwrite_csv_does_not_touch_api_rows():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))

    async def _api_row():
        db.add(ImportedFinanceRow(id="api1", import_id="x", user_id=uid, marketplace="wildberries",
                                  date="2026-07-01", sku="A", revenue=999, commission=0, logistics=0,
                                  ad_spend=0, net_profit=0, quantity=0,
                                  marketplace_account_id=s.account_id, marketplace_store_id=s.store_id,
                                  source="api"))
        await db.commit()
    _run(_api_row())
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("A"), "finance")
    _upload_confirm(c, s.store_id, _finance("A"), "finance", mode="overwrite")
    # the api-sourced row survives (source filter)
    assert _run(db.get(ImportedFinanceRow, "api1")) is not None


def test_card_overwrite_only_this_account():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    other = _run(_extra_store(db, s.ws, "ozon"))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _card("C1"), "card_content")
    _upload_confirm(c, other.store_id, _card("C1"), "card_content")
    _, cf = _upload_confirm(c, s.store_id, _card("C1"), "card_content", mode="overwrite")
    assert cf.json()["replaced"] == 1                              # only this account's card row
    other_rows = _run(db.execute(select(ImportedCardContentRow).where(
        ImportedCardContentRow.marketplace_account_id == other.account_id))).scalars().all()
    assert len(other_rows) == 1                                    # other account untouched


def test_preview_rows_to_replace_matches_confirm():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("A"), "finance")
    prev = _upload(c, s.store_id, _finance("A"), "finance")
    rows_to_replace = prev.json()["rows_to_replace"]
    cf = c.post(f"/api/import/{prev.json()['import_id']}/confirm", json={"mode": "overwrite"})
    assert rows_to_replace == cf.json()["replaced"] == 1


def test_overwrite_different_stores_do_not_mix():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid, "yandex", uuid.uuid4().hex))
    other = _run(_extra_store(db, s.ws, "yandex"))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("A"), "finance", mode="overwrite")
    _upload_confirm(c, other.store_id, _finance("A"), "finance", mode="overwrite")
    assert _run(finance_aggregator.store_financial_totals(s.store_id, db))["revenue"] == 1000.0
    assert _run(finance_aggregator.store_financial_totals(other.store_id, db))["revenue"] == 1000.0


# ── conflict endpoints ────────────────────────────────────────────────────────
def test_get_conflicts_scoped_and_candidates_same_account():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    c = _client(db, uid)
    up, _ = _upload_confirm(c, s.store_id, _finance("DUP"), "finance")
    iid = up.json()["import_id"]
    res = c.get(f"/api/import/{iid}/conflicts")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1 and rows[0]["sku"] == "DUP"
    assert len(rows[0]["candidates"]) == 2                         # both same-account products
    # foreign user cannot see it
    other_c = _client(db, str(uuid.uuid4()))
    assert other_c.get(f"/api/import/{iid}/conflicts").status_code == 404


def test_resolve_link_existing_and_cross_account_blocked():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    foreign = _run(_extra_store(db, s.ws, "ozon"))

    async def _foreign_prod():
        db.add(Product(id="foreignP", user_id=uid, name="F", marketplace="ozon",
                       sku="DUP", marketplace_account_id=foreign.account_id)); await db.commit()
    _run(_foreign_prod())

    c = _client(db, uid)
    up, _ = _upload_confirm(c, s.store_id, _finance("DUP"), "finance")
    iid = up.json()["import_id"]
    row_id = c.get(f"/api/import/{iid}/conflicts").json()[0]["row_id"]
    cand = c.get(f"/api/import/{iid}/conflicts").json()[0]["candidates"][0]["product_id"]
    # cross-account product rejected
    bad = c.post(f"/api/import/{iid}/conflicts/resolve",
                 json={"row_id": row_id, "action": "link_existing", "product_id": "foreignP"})
    assert bad.status_code == 404
    # link to a same-account candidate works + creates a placement
    ok = c.post(f"/api/import/{iid}/conflicts/resolve",
                json={"row_id": row_id, "action": "link_existing", "product_id": cand})
    assert ok.status_code == 200 and ok.json()["link_status"] == "linked"
    row = _run(db.get(ImportedFinanceRow, row_id))
    assert row.product_id == cand
    assert _run(db.execute(select(ProductPlacement).where(
        ProductPlacement.product_id == cand, ProductPlacement.marketplace_store_id == s.store_id))).scalars().first() is not None
    # idempotent
    again = c.post(f"/api/import/{iid}/conflicts/resolve",
                   json={"row_id": row_id, "action": "link_existing", "product_id": cand})
    assert again.status_code == 200 and again.json()["link_status"] == "linked"


def test_resolve_create_new_and_leave_unassigned():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    c = _client(db, uid)
    up, _ = _upload_confirm(c, s.store_id, _products("DUP"), "products")
    iid = up.json()["import_id"]
    row_id = c.get(f"/api/import/{iid}/conflicts").json()[0]["row_id"]
    r = c.post(f"/api/import/{iid}/conflicts/resolve", json={"row_id": row_id, "action": "create_new"})
    assert r.status_code == 200 and r.json()["link_status"] == "linked"
    assert r.json()["product_id"] is not None

    # a second conflict left unassigned
    db2 = _run(_new_db()); uid2 = str(uuid.uuid4()); s2 = _run(_store(db2, uid2))
    _run(_dupe_products(db2, uid2, s2.account_id, "DUP"))
    c2 = _client(db2, uid2)
    up2, _ = _upload_confirm(c2, s2.store_id, _finance("DUP"), "finance")
    iid2 = up2.json()["import_id"]
    rid2 = c2.get(f"/api/import/{iid2}/conflicts").json()[0]["row_id"]
    r2 = c2.post(f"/api/import/{iid2}/conflicts/resolve",
                 json={"row_id": rid2, "action": "leave_unassigned"})
    assert r2.json()["link_status"] == "unassigned" and r2.json()["product_id"] is None


# ── aggregates: unassigned in store sum, conflict excluded ────────────────────
def test_unassigned_in_store_sum_conflict_excluded():
    db = _run(_new_db()); uid = str(uuid.uuid4()); s = _run(_store(db, uid))
    _run(_dupe_products(db, uid, s.account_id, "DUP"))
    c = _client(db, uid)
    _upload_confirm(c, s.store_id, _finance("MISS"), "finance")    # unassigned money
    _upload_confirm(c, s.store_id, _finance("DUP"), "finance")     # conflict money
    totals = _run(finance_aggregator.store_financial_totals(s.store_id, db))
    # unassigned counts in the store total; conflict does NOT
    assert totals["revenue"] == 1000.0
    assert totals["unassigned_revenue"] == 1000.0
