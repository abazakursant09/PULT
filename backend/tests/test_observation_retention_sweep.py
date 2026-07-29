"""PULT-LAUNCH-2.5E-2B-2 — async observation-retention sweep (feature OFF).

Exercises the sweep against an in-memory SQLite DB (the service's engine/session are monkeypatched onto
the test engine; settings.observation_retention_enabled is flipped ONLY inside a test, never in config).
Covers: feature-OFF (no lock, no SQL), latest-always-kept, the 100->90->100 history, resolved-180 /
unassigned-30 cutoffs on fetched_at, account/series isolation, parent->children CASCADE, the lock path,
dry-run counting, batching, and timezone boundaries. A real-PostgreSQL functional DELETE test skips
locally and runs in the postgres-explain CI job.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as PO, MarketplacePromotionStoreEvidence as SE)
import services.marketplace.retention.observation_sweep as obs

T0 = datetime(2026, 7, 29, 12, 0, 0)
_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _mk_db(fk: bool = False):
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if fk:
        @event.listens_for(e.sync_engine, "connect")
        def _fk(dbapi_con, _rec):
            dbapi_con.execute("PRAGMA foreign_keys=ON")
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return e, sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


def _use(monkeypatch, e, Session, *, enabled=True):
    monkeypatch.setattr(obs, "engine", e)
    monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
    monkeypatch.setattr(obs.settings, "observation_retention_enabled", enabled)


def _price_row(**over):
    base = dict(marketplace_account_id="a", marketplace_store_id="s",
                product_id=None, external_product_id="E", resolution_status="unassigned",
                observation_kind="catalog", promotion_key="__none__", promotion_type=None,
                participation_status=None, currency_status="unknown", seller_revenue_status="unknown",
                commission_base_status="unknown", subsidy_status="unknown", source="api",
                fetched_at=T0, last_verified_at=T0, missing_fields=[], created_at=T0)
    base.update(over)
    base.setdefault("id", f"P-{base['external_product_id']}-{base['fetched_at'].isoformat()}-{base['marketplace_store_id']}")
    base.setdefault("ingest_run_id", base["id"])     # unique per row -> never a run-uniqueness collision
    if base["resolution_status"] == "resolved" and base["product_id"] is None:
        base["product_id"] = "prod"
    return base


def _promo_row(**over):
    base = dict(marketplace_account_id="a", marketplace="yandex", product_id=None,
                external_product_id="OF", resolution_status="unassigned", promotion_id="PR",
                promotion_type="yandex_promo", provider_status="AUTO", participation_status="active",
                auto_participation=True, attribution_status="account_wide", currency_status="unknown",
                source="api", provider_dataset="promos", fetched_at=T0, last_verified_at=T0,
                missing_fields=[], created_at=T0)
    base.update(over)
    base.setdefault("id", f"Q-{base['external_product_id']}-{base['promotion_id']}-{base['fetched_at'].isoformat()}")
    base.setdefault("ingest_run_id", base["id"])
    if base["resolution_status"] == "resolved" and base["product_id"] is None:
        base["product_id"] = "prod"
    return base


async def _seed(Session, price=(), promo=(), evidence=()):
    async with Session() as db:
        for r in price:
            await db.execute(MPO.__table__.insert().values(**_price_row(**r)))
        for r in promo:
            await db.execute(PO.__table__.insert().values(**_promo_row(**r)))
        for r in evidence:
            await db.execute(SE.__table__.insert().values(**r))
        await db.commit()


def _count(Session, model):
    async def _c():
        async with Session() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()
    return _run(_c())


def _ids(Session, model):
    async def _i():
        async with Session() as db:
            return {r[0] for r in (await db.execute(select(model.id))).all()}
    return _run(_i())


# ══ 9.1 FEATURE OFF ══════════════════════════════════════════════════════════════
def test_feature_off_no_delete_no_lock(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[dict(external_product_id="E", fetched_at=T0 - timedelta(days=400)),
                                dict(external_product_id="E", fetched_at=T0)]))
    _use(monkeypatch, e, Session, enabled=False)     # flag OFF
    for dry in (False, True):
        res = _run(obs.run_observation_retention(dry_run=dry, now=T0))
        assert res.enabled is False and res.lock_acquired is False
        assert res.price_removed == 0 and res.promotion_removed == 0
    assert _count(Session, MPO) == 2                 # DB unchanged


# ══ 9.2 / 9.3 LATEST + HISTORY ═══════════════════════════════════════════════════
def test_latest_always_kept_even_if_ancient(monkeypatch):
    e, Session = _run(_mk_db())
    # a single, very old row IS the latest of its series -> never deleted
    _run(_seed(Session, price=[dict(external_product_id="E", resolution_status="resolved",
                                    fetched_at=T0 - timedelta(days=400))],
               promo=[dict(external_product_id="OF", fetched_at=T0 - timedelta(days=400))]))
    _use(monkeypatch, e, Session)
    res = _run(obs.run_observation_retention(now=T0))
    assert res.price_removed == 0 and res.promotion_removed == 0
    assert _count(Session, MPO) == 1 and _count(Session, PO) == 1


def test_100_90_100_history(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)      # >180d, non-latest
    mid = T0 - timedelta(days=100)
    new = T0 - timedelta(days=10)       # latest
    _run(_seed(Session, price=[
        dict(id="A", external_product_id="E", resolution_status="resolved", fetched_at=old,
             last_verified_at=T0 + timedelta(days=5)),   # last_verified in the FUTURE must not save it
        dict(id="B", external_product_id="E", resolution_status="resolved", fetched_at=mid),
        dict(id="C", external_product_id="E", resolution_status="resolved", fetched_at=new)]))
    _use(monkeypatch, e, Session)
    res = _run(obs.run_observation_retention(now=T0))
    assert res.price_removed == 1
    assert _ids(Session, MPO) == {"B", "C"}          # ancient non-latest A gone; mid + latest kept


def test_all_within_window_kept(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[
        dict(id="A", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=100)),
        dict(id="B", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=50)),
        dict(id="C", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=1))]))
    _use(monkeypatch, e, Session)
    _run(obs.run_observation_retention(now=T0))
    assert _ids(Session, MPO) == {"A", "B", "C"}


# ══ 9.4 UNASSIGNED ═══════════════════════════════════════════════════════════════
def test_unassigned_30d_and_resolved_180d_cutoffs(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[
        # unassigned series: old non-latest (35d) deleted, latest kept
        dict(id="U_old", external_product_id="U", resolution_status="unassigned", fetched_at=T0 - timedelta(days=35)),
        dict(id="U_new", external_product_id="U", resolution_status="unassigned", fetched_at=T0 - timedelta(days=1)),
        # resolved series: a 31-day-old non-latest is NOT deleted (well within 180d)
        dict(id="R_31", external_product_id="R", resolution_status="resolved", fetched_at=T0 - timedelta(days=31)),
        dict(id="R_new", external_product_id="R", resolution_status="resolved", fetched_at=T0)]))
    _use(monkeypatch, e, Session)
    res = _run(obs.run_observation_retention(now=T0))
    assert res.price_removed == 1
    assert _ids(Session, MPO) == {"U_new", "R_31", "R_new"}


# ══ 9.5 ISOLATION ════════════════════════════════════════════════════════════════
def test_account_isolation(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)
    _run(_seed(Session, price=[
        dict(id="a_old", marketplace_account_id="a", marketplace_store_id="sa", external_product_id="E",
             resolution_status="resolved", fetched_at=old),
        dict(id="a_new", marketplace_account_id="a", marketplace_store_id="sa", external_product_id="E",
             resolution_status="resolved", fetched_at=T0),
        # account b: same external_product_id, its own store; a SINGLE ancient row = latest of b's series
        # -> protected. It must survive (proves a's sweep never reaches b's data).
        dict(id="b_only", marketplace_account_id="b", marketplace_store_id="sb", external_product_id="E",
             resolution_status="resolved", fetched_at=old)]))
    _use(monkeypatch, e, Session)
    _run(obs.run_observation_retention(now=T0))
    assert _ids(Session, MPO) == {"a_new", "b_only"}   # a's ancient non-latest gone; a latest + b intact


def test_two_stores_same_account_are_distinct_series(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)
    _run(_seed(Session, price=[
        # store s1: old is non-latest -> deletable
        dict(id="s1_old", marketplace_store_id="s1", external_product_id="E", resolution_status="resolved", fetched_at=old),
        dict(id="s1_new", marketplace_store_id="s1", external_product_id="E", resolution_status="resolved", fetched_at=T0),
        # store s2: only one (old) row -> it is the latest of s2's series -> kept
        dict(id="s2_only", marketplace_store_id="s2", external_product_id="E", resolution_status="resolved", fetched_at=old)]))
    _use(monkeypatch, e, Session)
    _run(obs.run_observation_retention(now=T0))
    assert _ids(Session, MPO) == {"s1_new", "s2_only"}


# ══ 9.6 PARENT / CHILDREN CASCADE ════════════════════════════════════════════════
def test_parent_delete_cascades_children(monkeypatch):
    e, Session = _run(_mk_db(fk=True))
    # minimal parents for the composite FKs (FK ON)
    async def _seed_fk():
        async with Session() as db:
            await db.execute(text("INSERT INTO users(id,email,name,hashed_password) VALUES('u','a@b.c','A','x')"))
            await db.execute(text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('w','u',CURRENT_TIMESTAMP)"))
            await db.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) VALUES('a','w','yandex','verified')"))
            await db.execute(text("INSERT INTO marketplace_stores(id,marketplace_account_id,marketplace,store_key,external_store_id,label,source,status,created_at,updated_at) VALUES('st','a','yandex','st','c1','S','api','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            await db.commit()
    _run(_seed_fk())
    old = T0 - timedelta(days=200)
    _run(_seed(Session,
               promo=[dict(id="Q_old", external_product_id="OF", promotion_id="PR",
                           provider_status="PARTIALLY_AUTO", attribution_status="exact_stores", fetched_at=old),
                      dict(id="Q_new", external_product_id="OF", promotion_id="PR",
                           provider_status="PARTIALLY_AUTO", attribution_status="exact_stores", fetched_at=T0)],
               evidence=[dict(id="SE_old", promotion_observation_id="Q_old", marketplace_account_id="a",
                              external_store_id="c1", marketplace_store_id="st", mapping_status="mapped", created_at=old),
                         dict(id="SE_new", promotion_observation_id="Q_new", marketplace_account_id="a",
                              external_store_id="c1", marketplace_store_id="st", mapping_status="mapped", created_at=T0)]))
    _use(monkeypatch, e, Session)
    res = _run(obs.run_observation_retention(now=T0))
    assert res.promotion_removed == 1
    assert _ids(Session, PO) == {"Q_new"}
    assert _ids(Session, SE) == {"SE_new"}            # SE_old removed by CASCADE, never directly


# ══ 9.7 LOCK ═════════════════════════════════════════════════════════════════════
def test_second_run_gets_no_lock_and_deletes_nothing(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[dict(id="A", external_product_id="E", resolution_status="resolved",
                                    fetched_at=T0 - timedelta(days=400)),
                               dict(id="B", external_product_id="E", resolution_status="resolved", fetched_at=T0)]))
    _use(monkeypatch, e, Session)

    async def _both():
        await obs._local_lock.acquire()               # simulate a concurrent run holding the lock
        try:
            return await obs.run_observation_retention(now=T0)
        finally:
            obs._local_lock.release()
    res = _run(_both())
    assert res.lock_acquired is False and res.price_removed == 0
    assert _count(Session, MPO) == 2                  # nothing deleted while lock held


# ══ 9.8 DRY-RUN ══════════════════════════════════════════════════════════════════
def test_dry_run_counts_but_deletes_nothing_and_is_repeatable(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)
    _run(_seed(Session, price=[dict(id=f"A{i}", external_product_id="E", resolution_status="resolved",
                                    fetched_at=old + timedelta(hours=i)) for i in range(4)]
                              + [dict(id="LATEST", external_product_id="E", resolution_status="resolved", fetched_at=T0)]))
    _use(monkeypatch, e, Session)
    r1 = _run(obs.run_observation_retention(dry_run=True, now=T0, batch_size=2))
    r2 = _run(obs.run_observation_retention(dry_run=True, now=T0, batch_size=2))
    assert r1.price_candidates == 4 and r2.price_candidates == 4     # count > batch_size, repeatable
    assert r1.price_removed == 0 and _count(Session, MPO) == 5       # nothing deleted
    # after a real sweep, candidates drop to 0 (only the protected latest remains)
    _run(obs.run_observation_retention(now=T0, batch_size=2))
    r3 = _run(obs.run_observation_retention(dry_run=True, now=T0))
    assert r3.price_candidates == 0 and _count(Session, MPO) == 1


# ══ 9.9 BATCH ════════════════════════════════════════════════════════════════════
def test_batching_multiple_commits_and_idempotent(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)
    rows = [dict(id=f"A{i}", external_product_id="E", resolution_status="resolved",
                 fetched_at=old + timedelta(hours=i)) for i in range(7)]        # 7 old non-latest
    rows.append(dict(id="LATEST", external_product_id="E", resolution_status="resolved", fetched_at=T0))
    _run(_seed(Session, price=rows))
    _use(monkeypatch, e, Session)
    res = _run(obs.run_observation_retention(now=T0, batch_size=2))
    assert res.price_removed == 7 and res.batches >= 4                # multiple commits
    assert _ids(Session, MPO) == {"LATEST"}
    res2 = _run(obs.run_observation_retention(now=T0, batch_size=2))  # idempotent
    assert res2.price_removed == 0 and _count(Session, MPO) == 1


def test_batch_size_validation(monkeypatch):
    e, Session = _run(_mk_db())
    _use(monkeypatch, e, Session)
    for bad in (0, -1, obs._MAX_BATCH_SIZE + 1):
        with pytest.raises(ValueError):
            _run(obs.run_observation_retention(now=T0, batch_size=bad))


# ══ 9.10 TIMEZONE ════════════════════════════════════════════════════════════════
def test_exact_180_boundary_kept_over_181_deleted(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[
        dict(id="at180", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=180)),
        dict(id="at181", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=181)),
        dict(id="new", external_product_id="E", resolution_status="resolved", fetched_at=T0)]))
    _use(monkeypatch, e, Session)
    _run(obs.run_observation_retention(now=T0))
    # fetched_at < cutoff: exactly-180 is NOT < cutoff -> kept; 181 is older -> deleted
    assert _ids(Session, MPO) == {"at180", "new"}


def test_aware_now_offsets_give_same_cutoff(monkeypatch):
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[dict(id="old", external_product_id="E", resolution_status="resolved",
                                    fetched_at=T0 - timedelta(days=200)),
                               dict(id="new", external_product_id="E", resolution_status="resolved", fetched_at=T0)]))
    _use(monkeypatch, e, Session)
    # 12:00 UTC == 15:00+03:00 — same instant, same cutoff, same result
    msk = datetime(2026, 7, 29, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    _run(obs.run_observation_retention(now=msk))
    assert _ids(Session, MPO) == {"new"}


# ══ 9.12 GUARD ═══════════════════════════════════════════════════════════════════
def test_guard_no_wiring():
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    sched = (backend / "tasks" / "scheduler.py").read_text(encoding="utf-8")
    assert "observation_retention" not in sched and "observation_sweep" not in sched
    from config import settings
    assert settings.observation_retention_enabled is False
    assert settings.api_data_sync_enabled is False
    assert settings.automation_enabled is False
    # the sweep never writes / never touches provider or ingest
    src = (backend / "services" / "marketplace" / "retention" / "observation_sweep.py").read_text(encoding="utf-8")
    assert "INSERT INTO" not in src.upper()
    for bad in ("requests.", "httpx.", "provider", "updatePromoOffers", "deletePromoOffers"):
        assert bad not in src


# ══ 9.11 REAL PostgreSQL functional DELETE (skips locally; runs in postgres-explain CI job) ══════════
def test_pg_functional_delete(monkeypatch):
    url = os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")
    if not url or not url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); functional DELETE runs "
                    "in the postgres-explain CI job on real PostgreSQL 16.")
    e = create_async_engine(url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://"))
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        async with e.begin() as c:                    # self-isolating: own fresh schema
            await c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")   # asyncpg: one command per call
            await c.exec_driver_sql("CREATE SCHEMA public")
            await c.run_sync(Base.metadata.create_all)
        old = T0 - timedelta(days=200)
        async with Session() as db:
            await db.execute(text("SET session_replication_role = replica"))   # bypass FK for synthetic seed
            await db.execute(MPO.__table__.insert().values(**_price_row(
                id="pg_old", external_product_id="E", resolution_status="resolved", fetched_at=old)))
            await db.execute(MPO.__table__.insert().values(**_price_row(
                id="pg_new", external_product_id="E", resolution_status="resolved", fetched_at=T0)))
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        res = await obs.run_observation_retention(now=T0)
        async with Session() as db:
            ids = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        await e.dispose()
        return res, ids
    res, ids = _run(_go())
    assert res.enabled and res.lock_acquired          # advisory lock really taken on PostgreSQL
    assert res.price_removed == 1 and ids == {"pg_new"}
