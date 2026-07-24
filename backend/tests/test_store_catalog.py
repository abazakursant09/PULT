"""PULT-LAUNCH-1.4.5B — read-only store catalog.

Covers the two new endpoints (products of one store, import history of one store) against an
in-memory SQLite whose CHECK/UNIQUE/composite-FK constraints from PULT-LAUNCH-1.3 do the real
enforcing. Router functions are called directly, the style used by the other store tests.

The point of most of these tests is isolation: a store must never show another store's
products, another cabinet's products, or another import's conflict rows.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from models.workspace import Workspace
from routers.store_catalog import store_imports, store_products

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _user(db):
    uid = str(uuid.uuid4())
    wid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    await db.commit()
    return await db.get(User, uid), wid


async def _account(db, workspace_id, marketplace="yandex", label="Кабинет"):
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=workspace_id,
                             marketplace=marketplace, identity_status="unverified", label=label)
    db.add(acc)
    await db.commit()
    return acc


async def _store(db, account, label="Магазин", status="active"):
    key = "primary" if account.marketplace in ("wildberries", "ozon") else uuid.uuid4().hex
    st = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=account.id,
                          marketplace=account.marketplace, store_key=key, label=label,
                          source="manual", status=status)
    db.add(st)
    await db.commit()
    return st


async def _product(db, user, account, sku, name):
    p = Product(id=str(uuid.uuid4()), user_id=str(user.id), name=name,
                marketplace=account.marketplace, sku=sku, marketplace_account_id=account.id)
    db.add(p)
    await db.commit()
    return p


async def _place(db, product, store, status="active", source="csv"):
    pl = ProductPlacement(id=str(uuid.uuid4()), product_id=product.id,
                          marketplace_store_id=store.id,
                          marketplace_account_id=store.marketplace_account_id,
                          status=status, source=source)
    db.add(pl)
    await db.commit()
    return pl


async def _import(db, user, store, *, import_type="products", status="confirmed",
                  filename="report.csv", created_at=None, total_rows=10, imported=8, skipped=2):
    rec = ImportRecord(
        id=str(uuid.uuid4()), user_id=str(user.id), filename=filename,
        file_hash=uuid.uuid4().hex, marketplace=store.marketplace, import_type=import_type,
        status=status, temp_path="/srv/uploads/secret/path.csv",
        marketplace_account_id=store.marketplace_account_id, marketplace_store_id=store.id,
        source="csv", total_rows=total_rows, valid_rows=total_rows, skipped_rows=skipped,
        imported_count=imported, created_at=created_at or datetime.utcnow(),
    )
    db.add(rec)
    await db.commit()
    return rec


async def _prow(db, rec, user, store, sku="SKU-1", link_status="conflict"):
    row = ImportedProductRow(
        id=str(uuid.uuid4()), import_id=rec.id, user_id=str(user.id),
        marketplace=store.marketplace, sku=sku, title="Товар",
        marketplace_account_id=store.marketplace_account_id, marketplace_store_id=store.id,
        source="csv", link_status=link_status)
    db.add(row)
    await db.commit()
    return row


class _CountingSession:
    """Transparent proxy that counts db.execute calls — how the N+1 test is proven."""

    def __init__(self, inner):
        self._inner = inner
        self.executes = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def execute(self, *args, **kwargs):
        self.executes += 1
        return await self._inner.execute(*args, **kwargs)


def _products(store_id, db, user, *, page=1, page_size=25, search=None, status=None):
    """FastAPI resolves Query() defaults; a direct call does not, so the tests fill them in."""
    return _run(store_products(store_id, page=page, page_size=page_size, search=search,
                               status=status, db=db, user=user))


def _imports(store_id, db, user, *, page=1, page_size=25, status=None, import_type=None):
    return _run(store_imports(store_id, page=page, page_size=page_size, status=status,
                              import_type=import_type, db=db, user=user))


# 1. Another workspace's store: products → 404
def test_foreign_store_products_404():
    db = _run(_new_db())
    owner, ws = _run(_user(db))
    stranger, _ = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    with pytest.raises(HTTPException) as e:
        _products(store.id, db, stranger)
    assert e.value.status_code == 404
    # A missing id must be indistinguishable from someone else's id.
    with pytest.raises(HTTPException) as missing:
        _products(str(uuid.uuid4()), db, stranger)
    assert missing.value.detail == e.value.detail


# 2. Another workspace's store: imports → 404
def test_foreign_store_imports_404():
    db = _run(_new_db())
    owner, ws = _run(_user(db))
    stranger, _ = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    _run(_import(db, owner, store))
    with pytest.raises(HTTPException) as e:
        _imports(store.id, db, stranger)
    assert e.value.status_code == 404


# 3. A product of store A never appears in store B
def test_product_of_store_a_not_in_store_b():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    a, b = _run(_store(db, acc, "A")), _run(_store(db, acc, "B"))
    p = _run(_product(db, user, acc, "SKU-A", "Товар A"))
    _run(_place(db, p, a))
    assert [i.product_id for i in _products(a.id, db, user).items] == [p.id]
    assert _products(b.id, db, user).items == []


# 4. The same Product in two Yandex stores shows up in each, through its own placement
def test_same_product_in_two_stores():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws, "yandex"))
    a, b = _run(_store(db, acc, "Москва")), _run(_store(db, acc, "Казань"))
    p = _run(_product(db, user, acc, "SKU-1", "Товар"))
    _run(_place(db, p, a, source="csv"))
    _run(_place(db, p, b, source="manual"))
    ra = _products(a.id, db, user)
    rb = _products(b.id, db, user)
    assert [i.product_id for i in ra.items] == [p.id] and ra.total == 1
    assert [i.product_id for i in rb.items] == [p.id] and rb.total == 1
    # Each row reports ITS placement, not a shared one.
    assert ra.items[0].placement_source == "csv"
    assert rb.items[0].placement_source == "manual"


# 5. A product with no sales and no metrics is still listed
def test_product_without_metrics_is_visible():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    p = _run(_product(db, user, acc, None, "Без метрик"))
    _run(_place(db, p, store))
    out = _products(store.id, db, user)
    assert out.total == 1 and out.items[0].sku is None
    # No metric field leaked into the row.
    assert set(out.items[0].model_dump()) == {
        "product_id", "sku", "name", "placement_status", "placement_source",
        "first_seen_at", "last_seen_at",
    }


# 6. An archived store is readable but refuses a new import
def test_archived_store_is_read_only():
    from routers.csv_import import _owned_active_store
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc, status="archived"))
    p = _run(_product(db, user, acc, "SKU-1", "Товар"))
    _run(_place(db, p, store))
    _run(_import(db, user, store))
    assert _products(store.id, db, user).total == 1
    assert _imports(store.id, db, user).total == 1
    with pytest.raises(HTTPException) as e:
        _run(_owned_active_store(db, user, store.id))
    assert e.value.status_code == 409


# 7-8. Search by SKU and by name
def test_search_by_sku_and_name():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    kettle = _run(_product(db, user, acc, "SKU-2210", "Чайник электрический"))
    coffee = _run(_product(db, user, acc, "SKU-1042", "Кофе зерновой"))
    for p in (kettle, coffee):
        _run(_place(db, p, store))
    by_sku = _products(store.id, db, user, search="2210")
    assert [i.product_id for i in by_sku.items] == [kettle.id] and by_sku.total == 1
    # ASCII is matched case-insensitively (SQLite folds ASCII only, Postgres folds everything).
    by_sku_lower = _products(store.id, db, user, search="sku-2210")
    assert [i.product_id for i in by_sku_lower.items] == [kettle.id]
    by_name = _products(store.id, db, user, search="Кофе")
    assert [i.product_id for i in by_name.items] == [coffee.id] and by_name.total == 1


# 9-10. Pagination and a stable order for products
def test_products_pagination_and_stable_order():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    for i in range(7):
        _run(_place(db, _run(_product(db, user, acc, f"SKU-{i}", f"Товар {i:02d}")), store))

    p1 = _products(store.id, db, user, page=1, page_size=3)
    p2 = _products(store.id, db, user, page=2, page_size=3)
    p3 = _products(store.id, db, user, page=3, page_size=3)
    assert (p1.total, p1.pages) == (7, 3)
    assert (len(p1.items), len(p2.items), len(p3.items)) == (3, 3, 1)
    ids = [i.product_id for i in p1.items + p2.items + p3.items]
    assert len(set(ids)) == 7                       # no repeats, nothing skipped
    assert ids == sorted(ids, key=lambda pid: next(
        i.name for i in p1.items + p2.items + p3.items if i.product_id == pid))
    # Same query twice returns the same order.
    assert [i.product_id for i in _products(store.id, db, user, page=1, page_size=3).items] == \
        [i.product_id for i in p1.items]


# 11. History holds only the chosen store's imports, newest first
def test_imports_only_for_this_store():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    a, b = _run(_store(db, acc, "A")), _run(_store(db, acc, "B"))
    now = datetime.utcnow()
    old = _run(_import(db, user, a, filename="old.csv", created_at=now - timedelta(days=2)))
    new = _run(_import(db, user, a, filename="new.csv", created_at=now))
    _run(_import(db, user, b, filename="other-store.csv"))
    out = _imports(a.id, db, user)
    assert [i.import_id for i in out.items] == [new.id, old.id]
    assert out.total == 2


# 12. Pagination of the history
def test_imports_pagination():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    base = datetime.utcnow()
    for i in range(5):
        _run(_import(db, user, store, filename=f"f{i}.csv", created_at=base - timedelta(hours=i)))
    p1 = _imports(store.id, db, user, page=1, page_size=2)
    p2 = _imports(store.id, db, user, page=2, page_size=2)
    assert (p1.total, p1.pages) == (5, 3)
    assert len(p1.items) == 2 and len(p2.items) == 2
    assert not {i.import_id for i in p1.items} & {i.import_id for i in p2.items}


# 13-14. status and import_type filters
def test_imports_filters():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    confirmed = _run(_import(db, user, store, status="confirmed", import_type="products"))
    pending = _run(_import(db, user, store, status="pending", import_type="finance"))
    only_confirmed = _imports(store.id, db, user, status="confirmed")
    assert [i.import_id for i in only_confirmed.items] == [confirmed.id]
    only_finance = _imports(store.id, db, user, import_type="finance")
    assert [i.import_id for i in only_finance.items] == [pending.id]


# 15-16. conflicts / unassigned are counted per import_id only
def test_counts_are_scoped_to_the_import():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    first = _run(_import(db, user, store, created_at=datetime.utcnow() - timedelta(hours=1)))
    second = _run(_import(db, user, store))
    _run(_prow(db, first, user, store, "SKU-1", "conflict"))
    _run(_prow(db, first, user, store, "SKU-2", "unassigned"))
    _run(_prow(db, first, user, store, "SKU-3", "linked"))
    _run(_prow(db, second, user, store, "SKU-9", "conflict"))

    by_id = {i.import_id: i for i in _imports(store.id, db, user).items}
    assert (by_id[first.id].conflicts, by_id[first.id].unassigned) == (1, 1)
    assert (by_id[second.id].conflicts, by_id[second.id].unassigned) == (1, 0)
    assert by_id[first.id].has_unresolved_conflicts is True


# 17. The counting must not grow one query per import
def test_no_n_plus_one_on_counts():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    one, many = _run(_store(db, acc, "Один")), _run(_store(db, acc, "Много"))
    rec = _run(_import(db, user, one))
    _run(_prow(db, rec, user, one))
    for _ in range(5):
        r = _run(_import(db, user, many))
        _run(_prow(db, r, user, many))

    counting = _CountingSession(db)
    _imports(one.id, counting, user)
    one_import = counting.executes
    counting.executes = 0
    _imports(many.id, counting, user)
    assert counting.executes == one_import        # 5x the imports, same number of queries


# 18. Resolving the last conflict clears the flag
def test_flag_clears_after_last_conflict_resolved():
    from routers.csv_import import ResolveRequest, resolve_conflict
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    rec = _run(_import(db, user, store))
    row = _run(_prow(db, rec, user, store, "SKU-1", "conflict"))

    before = _imports(store.id, db, user).items[0]
    assert before.has_unresolved_conflicts is True and before.conflicts == 1

    _run(resolve_conflict(rec.id, ResolveRequest(row_id=row.id, action="leave_unassigned"),
                          db=db, user=user))
    after = _imports(store.id, db, user).items[0]
    assert after.has_unresolved_conflicts is False
    assert (after.conflicts, after.unassigned) == (0, 1)


# 19. No credentials and no internal paths in the response
def test_response_has_no_secrets_or_paths():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    _run(_import(db, user, store))
    payload = _imports(store.id, db, user).model_dump()
    item = payload["items"][0]
    for forbidden in ("temp_path", "file_hash", "user_id", "marketplace_account_id"):
        assert forbidden not in item
    assert "/srv/uploads" not in str(payload)


# 20. Another cabinet in the same workspace is not revealed through a store
def test_other_account_products_not_revealed():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc_a = _run(_account(db, ws, "yandex", "Кабинет A"))
    acc_b = _run(_account(db, ws, "yandex", "Кабинет B"))
    store_a = _run(_store(db, acc_a, "A"))
    store_b = _run(_store(db, acc_b, "B"))
    p_a = _run(_product(db, user, acc_a, "SKU-A", "Товар A"))
    p_b = _run(_product(db, user, acc_b, "SKU-B", "Товар B"))
    _run(_place(db, p_a, store_a))
    _run(_place(db, p_b, store_b))
    out = _products(store_a.id, db, user)
    assert [i.product_id for i in out.items] == [p_a.id]


# 21. The row-model map must not drift from the import path's map
def test_row_model_map_matches_csv_import():
    from routers.csv_import import _ROW_MODEL as import_map
    from routers.store_catalog import _ROW_MODEL as catalog_map
    assert set(catalog_map) == set(import_map)
    assert all(catalog_map[k] is import_map[k] for k in catalog_map)


# 22. A status filter on placements narrows the product list
def test_placement_status_filter():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    live = _run(_product(db, user, acc, "SKU-1", "Живой"))
    gone = _run(_product(db, user, acc, "SKU-2", "Отвязанный"))
    _run(_place(db, live, store, status="active"))
    _run(_place(db, gone, store, status="detached"))
    out = _products(store.id, db, user, status="active")
    assert [i.product_id for i in out.items] == [live.id] and out.total == 1


# 23. An empty store answers cleanly, and finance rows count too
def test_empty_store_and_finance_rows_counted():
    db = _run(_new_db())
    user, ws = _run(_user(db))
    acc = _run(_account(db, ws))
    store = _run(_store(db, acc))
    empty = _products(store.id, db, user)
    assert (empty.total, empty.pages, empty.items) == (0, 0, [])
    assert empty.store.id == store.id and empty.store.status == "active"

    rec = _run(_import(db, user, store, import_type="finance"))
    db.add(ImportedFinanceRow(
        id=str(uuid.uuid4()), import_id=rec.id, user_id=str(user.id),
        marketplace=store.marketplace, date="2026-06-01", sku="SKU-1",
        marketplace_account_id=acc.id, marketplace_store_id=store.id,
        source="csv", link_status="conflict"))
    _run(db.commit())
    item = _imports(store.id, db, user).items[0]
    assert item.conflicts == 1 and item.has_unresolved_conflicts is True
