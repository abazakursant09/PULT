"""Real conflict handler concurrency in an owned disposable PG16 database.

No shared-schema resets, CSV files, providers or HTTP identity overrides. The
negative control removes only SELECT locking, scoped to its two handler calls.
"""
import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import Select

import test_startup_migration_pg as startup_pg
from database import Base
from models.user import User
from models.workspace import Workspace
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.import_record import ImportRecord
from models.imported_product import ImportedProductRow
from routers.csv_import import ResolveRequest, resolve_conflict

pg_startup = startup_pg.pg_startup


async def _seed(factory):
    owners = []
    async with factory() as db:
        for label in ("target", "unrelated"):
            uid, wid, aid, sid, pid, iid, rid = [str(uuid.uuid4()) for _ in range(7)]
            db.add(User(id=uid, email=f"{uid}@example.invalid", name=label,
                        hashed_password="synthetic-not-a-login-hash"))
            await db.flush()
            db.add(Workspace(id=wid, owner_user_id=uid))
            await db.flush()
            db.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="wildberries",
                                      identity_status="unverified", label=label))
            await db.flush()
            db.add(MarketplaceStore(id=sid, marketplace_account_id=aid, marketplace="wildberries",
                                    store_key="primary", label=label, source="manual", status="active"))
            db.add(Product(id=pid, user_id=uid, marketplace_account_id=aid,
                           marketplace="wildberries", sku="SYNTHETIC-CONFLICT", name=label))
            await db.flush()
            db.add(ProductPlacement(product_id=pid, marketplace_account_id=aid,
                                    marketplace_store_id=sid, status="active", source="csv"))
            db.add(ImportRecord(id=iid, user_id=uid, filename="synthetic.csv", file_hash=iid,
                                import_type="products", marketplace="wildberries", status="confirmed",
                                marketplace_account_id=aid, marketplace_store_id=sid))
            db.add(ImportedProductRow(id=rid, import_id=iid, user_id=uid, marketplace="wildberries",
                                      marketplace_account_id=aid, marketplace_store_id=sid,
                                      sku="SYNTHETIC-CONFLICT", title=label, link_status="conflict"))
            owners.append(SimpleNamespace(user=uid, workspace=wid, account=aid, store=sid,
                                          product=pid, import_id=iid, row=rid))
        await db.commit()
    return owners


async def _unrelated_snapshot(factory, owner):
    """All rows directly keyed to the seeded unrelated tenant, including placements."""
    values = {}
    keys = {"user_id": owner.user, "owner_user_id": owner.user,
            "workspace_id": owner.workspace, "marketplace_account_id": owner.account,
            "marketplace_store_id": owner.store, "product_id": owner.product,
            "import_id": owner.import_id}
    async with factory() as db:
        for table in Base.metadata.sorted_tables:
            predicates = [table.c[key] == value for key, value in keys.items() if key in table.c]
            if table.name == "users":
                predicates.append(table.c.id == owner.user)
            if not predicates:
                continue
            rows = (await db.execute(sa.select(table).where(sa.or_(*predicates)))).mappings()
            values[table.name] = sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
    return values


async def _projection(factory, owner):
    async with factory() as db:
        products = list((await db.execute(sa.select(Product.id).where(
            Product.marketplace_account_id == owner.account))).scalars())
        placements = list((await db.execute(sa.select(ProductPlacement.product_id).where(
            ProductPlacement.marketplace_store_id == owner.store))).scalars())
        row = await db.get(ImportedProductRow, owner.row)
        return {"new_products": sorted(pid for pid in products if pid != owner.product),
                "new_placements": sorted(pid for pid in placements if pid != owner.product),
                "row": (row.link_status, row.product_id)}


def _assert_single_resolution(results, projection):
    assert len(projection["new_products"]) == 1, "exactly one new Product required"
    pid = projection["new_products"][0]
    assert projection["new_placements"] == [pid]
    assert projection["row"] == ("linked", pid)
    assert all(r.product_id == pid and r.link_status == "linked" for r in results)


async def _race(engine, factory, owner, *, expect_lock):
    a_read, b_submitted, b_read = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release_a, release_b = asyncio.Event(), asyncio.Event()
    pids = {}

    class GatedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            is_row = isinstance(statement, Select) and any(
                d.get("entity") is ImportedProductRow for d in statement.column_descriptions
            )
            if is_row and self.info["role"] == "b":
                b_submitted.set()
            result = await super().execute(statement, *args, **kwargs)
            if is_row:
                # Materialize the real ORM result before pausing: the old unlocked
                # B must retain its observed conflict even after A commits.
                frozen = result.freeze()
                if self.info["role"] == "a":
                    a_read.set()
                    await release_a.wait()
                else:
                    b_read.set()
                    await release_b.wait()
                return frozen()
            return result

    async def call(role):
        async with GatedSession(engine, expire_on_commit=False, info={"role": role}) as db:
            # Short DB-side limits make cancellation/cleanup bounded as well.
            await db.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            await db.execute(sa.text("SET LOCAL lock_timeout = '8s'"))
            pids[role] = (await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
            return await resolve_conflict(owner.import_id,
                ResolveRequest(row_id=owner.row, action="create_new"),
                db=db, user=SimpleNamespace(id=owner.user))

    tasks = []
    try:
        async with asyncio.timeout(15):
            tasks.append(asyncio.create_task(call("a")))
            await a_read.wait()
            tasks.append(asyncio.create_task(call("b")))
            await b_submitted.wait()
            assert pids["a"] != pids["b"]
            # Never wait for both SELECT results: FOR UPDATE intentionally keeps
            # B inside execute until A commits. Observe that actual PG dependency.
            async with engine.connect() as observer:
                while not b_read.is_set():
                    if any(task.done() for task in tasks):
                        for task in tasks:
                            if task.done():
                                task.result()
                        raise AssertionError("handler completed before its release gate")
                    blockers = (await observer.execute(sa.text(
                        "SELECT pg_blocking_pids(:pid)"), {"pid": pids["b"]})).scalar_one()
                    if pids["a"] in blockers:
                        break
                else:
                    blockers = []
            observed_lock = pids["a"] in blockers
            assert observed_lock is expect_lock, "unexpected SELECT lock behavior"
            release_a.set()
            first = await tasks[0]  # A commit precedes unlocked B's continuation.
            release_b.set()
            second = await tasks[1]
            projection = await _projection(factory, owner)
            return [first, second], projection
    finally:
        release_a.set()
        release_b.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _run_case(pg_startup, monkeypatch, *, unlocked):
    _, dbm = pg_startup
    asyncio.run(dbm.run_migrations())

    async def go():
        # URL comes only from the strict, unique-DB pg_startup fixture.
        engine = create_async_engine(dbm.settings.database_url, poolclass=sa.pool.NullPool)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            owner, other = await _seed(factory)
            before_other = await _unrelated_snapshot(factory, other)
            if unlocked:
                with monkeypatch.context() as patch:
                    patch.setattr(Select, "with_for_update", lambda self, *a, **kw: self)
                    results, projection = await _race(engine, factory, owner, expect_lock=False)
                assert len(projection["new_products"]) == 2
                assert len(projection["new_placements"]) == 2
                with pytest.raises(AssertionError, match="exactly one new Product"):
                    _assert_single_resolution(results, projection)
            else:
                results, projection = await _race(engine, factory, owner, expect_lock=True)
                _assert_single_resolution(results, projection)
                async with factory() as db:
                    replay = await resolve_conflict(owner.import_id,
                        ResolveRequest(row_id=owner.row, action="create_new"),
                        db=db, user=SimpleNamespace(id=owner.user))
                _assert_single_resolution([replay], await _projection(factory, owner))
                assert await _projection(factory, owner) == projection
            assert await _unrelated_snapshot(factory, other) == before_other
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_pg_concurrent_create_new_is_serialized_and_replay_stable(pg_startup, monkeypatch):
    _run_case(pg_startup, monkeypatch, unlocked=False)


def test_pg_unlocked_negative_control_detects_duplicate_products(pg_startup, monkeypatch):
    _run_case(pg_startup, monkeypatch, unlocked=True)
