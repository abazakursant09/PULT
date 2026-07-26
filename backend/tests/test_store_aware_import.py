"""PULT-LAUNCH-1.4.3 — store-aware Product / ProductPlacement / Imported* on confirm.

Confirm now resolves products WITHIN the cabinet, writes account_id/store_id/source/fetched_at on
the rows, and maintains an idempotent ProductPlacement per (store, product). Ambiguous SKUs abort
the whole confirm. Uses the TestClient + dependency-override style; the cross-cabinet DB guarantee
is checked directly with foreign keys ON.
"""
import asyncio
import io
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
from rate_limit import limit_import
import models  # noqa: F401
from models.import_record import ImportRecord
from models.imported_card_content import ImportedCardContentRow
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
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


async def _ws(db, uid):
    w = str(uuid.uuid4()); db.add(Workspace(id=w, owner_user_id=uid)); await db.commit(); return w


async def _account(db, ws, marketplace="wildberries", label="K"):
    a = str(uuid.uuid4())
    db.add(MarketplaceAccount(id=a, workspace_id=ws, marketplace=marketplace,
                              identity_status="unverified", label=label))
    await db.commit(); return a


async def _store(db, account_id, marketplace="wildberries", store_key="primary", label="S"):
    s = str(uuid.uuid4())
    db.add(MarketplaceStore(id=s, marketplace_account_id=account_id, marketplace=marketplace,
                            store_key=store_key, label=label, source="manual", status="active"))
    await db.commit(); return s


def _finance(sku="ART-1"):
    return ("дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
            f"2026-07-01,{sku},Товар,1000,100,50,30,820,3\n").encode("utf-8")


def _products(sku="ART-1"):
    return ("артикул,название,цена,остаток,рейтинг,отзывы\n"
            f"{sku},Товар,100,5,4.5,10\n").encode("utf-8")


def _returns(sku="ART-1"):
    return ("дата,артикул,возвраты,сумма возврата,причина\n"
            f"2026-07-01,{sku},2,200,брак\n").encode("utf-8")


def _card(sku="ART-1"):
    return ("артикул,название,описание,бренд,категория,количество фото\n"
            f"{sku},Товар,Описание,Бренд,Категория,3\n").encode("utf-8")


def _upload_confirm(c, store_id, csv_bytes, import_type):
    up = c.post("/api/import/upload",
                files={"file": (f"{import_type}.csv", io.BytesIO(csv_bytes), "text/csv")},
                data={"marketplace_store_id": store_id, "import_type": import_type})
    assert up.status_code == 200, up.text
    iid = up.json()["import_id"]
    cf = c.post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    return up, cf


# 1 & 2. Same SKU in two accounts of the same marketplace → two different Products
def _two_accounts_same_sku(marketplace):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    ws = _run(_ws(db, uid))
    a1 = _run(_account(db, ws, marketplace, "A")); s1 = _run(_store(db, a1, marketplace))
    a2 = _run(_account(db, ws, marketplace, "B")); s2 = _run(_store(db, a2, marketplace))
    c = _client(db, uid)
    _upload_confirm(c, s1, _products("DUP"), "products")
    _upload_confirm(c, s2, _products("DUP"), "products")
    prods = _run(db.execute(select(Product).where(Product.sku == "DUP"))).scalars().all()
    assert len(prods) == 2
    assert {p.marketplace_account_id for p in prods} == {a1, a2}


def test_same_sku_two_wb_accounts_two_products():
    _two_accounts_same_sku("wildberries")


def test_same_sku_two_ozon_accounts_two_products():
    _two_accounts_same_sku("ozon")


# 3. One Yandex product in two stores → two placements
def test_yandex_product_two_stores_two_placements():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws, "yandex"))
    s1 = _run(_store(db, acc, "yandex", store_key=uuid.uuid4().hex, label="S1"))
    s2 = _run(_store(db, acc, "yandex", store_key=uuid.uuid4().hex, label="S2"))
    c = _client(db, uid)
    _upload_confirm(c, s1, _products("Y1"), "products")
    _upload_confirm(c, s2, _products("Y1"), "products")   # same product, second store
    prods = _run(db.execute(select(Product).where(Product.sku == "Y1"))).scalars().all()
    assert len(prods) == 1                                # one card in the cabinet
    pls = _run(db.execute(select(ProductPlacement).where(
        ProductPlacement.product_id == prods[0].id))).scalars().all()
    assert {p.marketplace_store_id for p in pls} == {s1, s2}


# 4 & 5. Repeat import: no second placement; first_seen kept, last_seen bumped
def test_repeat_import_one_placement_first_seen_kept():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    c = _client(db, uid)
    _upload_confirm(c, s, _products("R1"), "products")
    pl1 = _run(db.execute(select(ProductPlacement))).scalars().one()
    first_seen = pl1.first_seen_at
    _upload_confirm(c, s, _products("R1"), "products")
    pls = _run(db.execute(select(ProductPlacement))).scalars().all()
    assert len(pls) == 1
    assert pls[0].first_seen_at == first_seen
    assert pls[0].last_seen_at >= first_seen


# 6. A product of another account is never used
def test_other_account_product_not_used():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    a1 = _run(_account(db, ws, "wildberries", "A")); s1 = _run(_store(db, a1))
    a2 = _run(_account(db, ws, "wildberries", "B")); s2 = _run(_store(db, a2))
    # a2 already has a product with SKU X
    async def _seed_p():
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                       sku="X", marketplace_account_id=a2)); await db.commit()
    _run(_seed_p())
    c = _client(db, uid)
    _upload_confirm(c, s1, _products("X"), "products")    # import into a1
    # a NEW product is created under a1, not linked to a2's
    a1_prod = _run(db.execute(select(Product).where(
        Product.marketplace_account_id == a1, Product.sku == "X"))).scalars().all()
    assert len(a1_prod) == 1
    assert a1_prod[0].marketplace_account_id == a1


# 7 & 8 & 9. Ambiguous SKU → per-row conflict (PULT-LAUNCH-1.4.4 replaced the 1.4.3 abort):
# the row is saved with link_status='conflict', product_id NULL, never auto-picked; the import
# still confirms and reports the conflict count.
def test_ambiguous_sku_saved_as_conflict():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))

    async def _dupe():
        for _ in range(2):
            db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="D", marketplace="wildberries",
                           sku="DUP", marketplace_account_id=acc))
        await db.commit()
    _run(_dupe())

    c = _client(db, uid)
    up = c.post("/api/import/upload",
                files={"file": ("f.csv", io.BytesIO(_finance("DUP")), "text/csv")},
                data={"marketplace_store_id": s, "import_type": "finance"})
    iid = up.json()["import_id"]
    cf = c.post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert cf.status_code == 200 and cf.json()["conflicts"] == 1   # saved, not aborted
    row = _run(db.execute(select(ImportedFinanceRow))).scalars().one()
    assert row.link_status == "conflict" and row.product_id is None
    assert _run(db.get(ImportRecord, iid)).status == "confirmed"


# 10. Products missing → Product + Placement created
def test_products_missing_creates_product_and_placement():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    _upload_confirm(_client(db, uid), s, _products("NEW"), "products")
    p = _run(db.execute(select(Product).where(Product.sku == "NEW"))).scalars().one()
    assert p.marketplace_account_id == acc
    pl = _run(db.execute(select(ProductPlacement))).scalars().one()
    assert pl.product_id == p.id and pl.marketplace_store_id == s


# 11 & 14. Finance: missing keeps row w/o product; found links product + placement
def test_finance_missing_and_found():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    c = _client(db, uid)
    # missing product: finance row saved with store, product_id NULL, no placement
    _upload_confirm(c, s, _finance("MISS"), "finance")
    frow = _run(db.execute(select(ImportedFinanceRow).where(
        ImportedFinanceRow.sku == "MISS"))).scalars().one()
    assert frow.product_id is None
    assert frow.marketplace_store_id == s and frow.marketplace_account_id == acc
    assert _run(db.execute(select(ProductPlacement))).scalars().all() == []
    # now a product exists → finance links it + creates placement
    _upload_confirm(c, s, _products("FND"), "products")
    _upload_confirm(c, s, _finance("FND"), "finance")
    f2 = _run(db.execute(select(ImportedFinanceRow).where(
        ImportedFinanceRow.sku == "FND"))).scalars().one()
    assert f2.product_id is not None
    assert _run(db.execute(select(ProductPlacement).where(
        ProductPlacement.marketplace_store_id == s))).scalars().first() is not None


# 12. Returns missing → no product, row saved with store
def test_returns_missing_saved_with_store():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    _upload_confirm(_client(db, uid), s, _returns("RM"), "returns")
    r = _run(db.execute(select(ImportedReturnRow).where(ImportedReturnRow.sku == "RM"))).scalars().one()
    assert r.product_id is None
    assert r.marketplace_store_id == s and r.marketplace_account_id == acc
    assert _run(db.execute(select(Product))).scalars().all() == []


# 13 & 18. Card-content missing → no product; row has account + source + fetched_at, no store_id
def test_card_content_account_level():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    _upload_confirm(_client(db, uid), s, _card("CC"), "card_content")
    row = _run(db.execute(select(ImportedCardContentRow))).scalars().one()
    assert row.product_id is None
    assert row.marketplace_account_id == acc
    assert row.source == "csv" and row.fetched_at is not None
    assert not hasattr(row, "marketplace_store_id") or getattr(row, "marketplace_store_id", None) is None


# 15/16/17. Imported rows carry account/store/source/fetched_at
def test_rows_carry_account_store_source_fetched():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    c = _client(db, uid)
    _upload_confirm(c, s, _products("P"), "products")
    _upload_confirm(c, s, _finance("P"), "finance")
    _upload_confirm(c, s, _returns("P"), "returns")
    for model in (ImportedProductRow, ImportedFinanceRow, ImportedReturnRow):
        row = _run(db.execute(select(model))).scalars().first()
        assert row.marketplace_account_id == acc
        assert row.marketplace_store_id == s
        assert row.source == "csv" and row.fetched_at is not None


# 19 & 20. Store A metrics exclude store B; unassigned rows counted in the store total
def test_store_scoped_totals():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws, "yandex"))
    sa = _run(_store(db, acc, "yandex", store_key=uuid.uuid4().hex, label="A"))
    sb = _run(_store(db, acc, "yandex", store_key=uuid.uuid4().hex, label="B"))
    c = _client(db, uid)
    _upload_confirm(c, sa, _finance("MISS-A"), "finance")   # unassigned money in store A
    _upload_confirm(c, sb, _finance("MISS-B"), "finance")   # money in store B
    ta = _run(finance_aggregator.store_financial_totals(sa, db))
    tb = _run(finance_aggregator.store_financial_totals(sb, db))
    assert ta["revenue"] == 1000.0 and tb["revenue"] == 1000.0        # each store only its own
    assert ta["unassigned_revenue"] == 1000.0                         # no product → still in store total


# 21 & 22. Confirm atomic; repeat confirm creates no duplicates
def test_confirm_atomic_no_duplicate_on_repeat():
    db = _run(_new_db()); uid = str(uuid.uuid4()); ws = _run(_ws(db, uid))
    acc = _run(_account(db, ws)); s = _run(_store(db, acc))
    c = _client(db, uid)
    up, cf = _upload_confirm(c, s, _products("A1"), "products")
    assert cf.status_code == 200
    iid = up.json()["import_id"]
    n_rows = len(_run(db.execute(select(ImportedProductRow))).scalars().all())
    n_pl = len(_run(db.execute(select(ProductPlacement))).scalars().all())
    again = c.post(f"/api/import/{iid}/confirm", json={"mode": "new"})
    assert again.status_code == 400                                   # already confirmed
    assert len(_run(db.execute(select(ImportedProductRow))).scalars().all()) == n_rows
    assert len(_run(db.execute(select(ProductPlacement))).scalars().all()) == n_pl


# 23. Cross-cabinet placement rejected by the DB (foreign keys ON)
def test_cross_cabinet_placement_rejected():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng.sync_engine, "connect")
    def _fk(dbapi, _rec):
        dbapi.execute("PRAGMA foreign_keys=ON")

    async def go():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        db = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()
        uid = str(uuid.uuid4()); ws = str(uuid.uuid4())
        db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x"))
        db.add(Workspace(id=ws, owner_user_id=uid))
        await db.commit()
        a1 = str(uuid.uuid4()); a2 = str(uuid.uuid4())
        db.add(MarketplaceAccount(id=a1, workspace_id=ws, marketplace="wildberries",
                                  identity_status="unverified", label="A"))
        db.add(MarketplaceAccount(id=a2, workspace_id=ws, marketplace="ozon",
                                  identity_status="unverified", label="B"))
        await db.commit()
        p1 = str(uuid.uuid4())
        db.add(Product(id=p1, user_id=uid, name="P", marketplace="wildberries",
                       sku="P", marketplace_account_id=a1))
        s2 = str(uuid.uuid4())
        db.add(MarketplaceStore(id=s2, marketplace_account_id=a2, marketplace="ozon",
                                store_key="primary", label="S", source="manual", status="active"))
        await db.commit()
        # product of a1 placed in a2's store — declared account a1 → store composite FK fails
        db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=p1, marketplace_store_id=s2,
                                marketplace_account_id=a1, source="csv", status="active"))
        raised = False
        try:
            await db.commit()
        except IntegrityError:
            raised = True
        await db.close()
        return raised

    assert _run(go()) is True


# 24. Single alembic head unchanged (no migration in 1.4.3)
def test_single_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["pad1a2b3c4d01"]
