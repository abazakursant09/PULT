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


# Secret identifiers that a naive SQLAlchemy exception / traceback would carry (account/store/SKU/
# external/promotion + a SQL parameter). None of these may reach the logs.
_SECRETS = ("SECRET-ACCT-uuid", "SECRET-STORE-uuid", "SECRET-SKU", "SECRET-EXT", "SECRET-PROMO")


def _boom_sessionmaker(real_session, *, boom_acc=None, boom_table="marketplace_price_observations"):
    """A sessionmaker whose DELETE on `boom_table` (optionally only for account `boom_acc`) raises an
    exception laden with secret identifiers + SQL parameters — everything else delegates to a real
    session. Simulates a mid-batch DB failure to prove the log is safe and the account stops."""
    class _Wrap:
        def __init__(self):
            self._real = real_session()

        async def __aenter__(self):
            self._s = await self._real.__aenter__()
            return self

        async def __aexit__(self, *a):
            return await self._real.__aexit__(*a)

        async def execute(self, stmt, params=None, *a, **k):
            s = str(stmt)
            if "DELETE" in s.upper() and boom_table in s \
                    and (boom_acc is None or (params or {}).get("acc") == boom_acc):
                raise RuntimeError(
                    "simulated DB failure " + " ".join(_SECRETS)
                    + " [SQL: DELETE FROM marketplace_price_observations WHERE ...]"
                    + " [parameters: {'acc': '" + _SECRETS[0] + "'}]")
            return await self._s.execute(stmt, params, *a, **k)

        async def commit(self):
            return await self._s.commit()

        async def rollback(self):
            return await self._s.rollback()

    return _Wrap


# ══ FIX 1 — SAFE LOGS ════════════════════════════════════════════════════════════
def test_batch_error_log_has_no_secrets_and_no_traceback(monkeypatch, caplog):
    import logging
    e, Session = _run(_mk_db())
    _run(_seed(Session, price=[
        dict(id="old", external_product_id="E", resolution_status="resolved", fetched_at=T0 - timedelta(days=400)),
        dict(id="new", external_product_id="E", resolution_status="resolved", fetched_at=T0)]))
    monkeypatch.setattr(obs, "engine", e)
    monkeypatch.setattr(obs, "AsyncSessionLocal", _boom_sessionmaker(Session))
    monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
    # An earlier migration test's alembic fileConfig can disable app loggers; re-enable + target this one.
    monkeypatch.setattr(obs.logger, "disabled", False)
    monkeypatch.setattr(obs.logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=obs.logger.name):
        res = _run(obs.run_observation_retention(now=T0))
    assert res.failed_batches == 1 and res.price_removed == 0
    txt = caplog.text
    for secret in _SECRETS:
        assert secret not in txt, secret
    assert "Traceback" not in txt and "[SQL:" not in txt and "parameters" not in txt
    assert "batch failed" in txt and "failed_batches=1" in txt


# ══ FIX 2 — ERROR STOPS THE WHOLE ACCOUNT ════════════════════════════════════════
def test_price_error_stops_account_promotion_but_next_account_runs(monkeypatch):
    e, Session = _run(_mk_db())
    old = T0 - timedelta(days=200)
    _run(_seed(Session,
               price=[dict(id="A_p_old", marketplace_account_id="A", marketplace_store_id="sA",
                           external_product_id="E", resolution_status="resolved", fetched_at=old),
                      dict(id="A_p_new", marketplace_account_id="A", marketplace_store_id="sA",
                           external_product_id="E", resolution_status="resolved", fetched_at=T0),
                      dict(id="B_p_old", marketplace_account_id="B", marketplace_store_id="sB",
                           external_product_id="E", resolution_status="resolved", fetched_at=old),
                      dict(id="B_p_new", marketplace_account_id="B", marketplace_store_id="sB",
                           external_product_id="E", resolution_status="resolved", fetched_at=T0)],
               promo=[dict(id="A_q_old", marketplace_account_id="A", external_product_id="OF",
                           promotion_id="PR", fetched_at=old),
                      dict(id="A_q_new", marketplace_account_id="A", external_product_id="OF",
                           promotion_id="PR", fetched_at=T0)]))
    # only account A's PRICE delete fails -> A promotion must NOT run (on 55fcc46 it did, deleting A_q_old)
    monkeypatch.setattr(obs, "engine", e)
    monkeypatch.setattr(obs, "AsyncSessionLocal", _boom_sessionmaker(Session, boom_acc="A"))
    monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
    res = _run(obs.run_observation_retention(now=T0))
    assert res.failed_batches == 1                                   # exactly one, for account A
    price_ids, promo_ids = _ids(Session, MPO), _ids(Session, PO)
    assert {"A_p_old", "A_p_new"} <= price_ids                       # A price batch rolled back -> intact
    assert {"A_q_old", "A_q_new"} <= promo_ids                       # A promotion never swept -> intact
    assert "B_p_old" not in price_ids and "B_p_new" in price_ids     # B cleaned by a fresh session
    # once the fault clears, a normal re-run cleans account A too
    monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
    _run(obs.run_observation_retention(now=T0))
    assert _ids(Session, MPO) == {"A_p_new", "B_p_new"} and _ids(Session, PO) == {"A_q_new"}


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
def test_guard_flags_off_and_sweep_is_read_delete_only():
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    from config import settings
    # PULT-LAUNCH-2.5E-3B WIRES retention into the scheduler, but every master switch stays OFF and the
    # dry-run switch defaults True (fail-safe).
    assert settings.observation_retention_enabled is False
    assert settings.observation_retention_dry_run is True
    assert settings.api_data_sync_enabled is False
    assert settings.automation_enabled is False
    sched = (backend / "tasks" / "scheduler.py").read_text(encoding="utf-8")
    assert sched.count("while True") == 1              # exactly ONE loop — no second scheduler
    assert "_observation_retention_tick" in sched      # wired via a tick, not an inline await of the sweep
    # the sweep never writes rows / never touches provider or ingest
    src = (backend / "services" / "marketplace" / "retention" / "observation_sweep.py").read_text(encoding="utf-8")
    assert "INSERT INTO" not in src.upper()
    for bad in ("requests.", "httpx.", "provider", "updatePromoOffers", "deletePromoOffers"):
        assert bad not in src


# ══ REAL PostgreSQL 16 (skip locally; run with 0 skipped in the postgres-explain CI job) ═════════════
def _pg_engine_or_skip():
    url = os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")
    if not url or not url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs on real "
                    "PostgreSQL 16 in the postgres-explain CI job.")
    return create_async_engine(
        url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://"))


async def _pg_reset(e):
    async with e.begin() as c:                    # own fresh schema (asyncpg: one command per call)
        await c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        await c.exec_driver_sql("CREATE SCHEMA public")
        await c.run_sync(Base.metadata.create_all)


async def _pg_seed_valid(Session):
    """Real, valid, related rows so FK / CASCADE work for real — NO session_replication_role=replica."""
    async with Session() as db:
        await db.execute(text("INSERT INTO users(id,email,name,hashed_password) VALUES('u','a@b.c','A','x')"))
        await db.execute(text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('w','u',now())"))
        await db.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                              "VALUES('a','w','yandex','verified')"))
        await db.execute(text("INSERT INTO marketplace_stores(id,marketplace_account_id,marketplace,store_key,"
                              "external_store_id,label,source,status,created_at,updated_at) "
                              "VALUES('st','a','yandex','st','c1','S','api','active',now(),now())"))
        await db.commit()


def _use_pg(monkeypatch, e, Session):
    monkeypatch.setattr(obs, "engine", e)
    monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
    monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)


# ── FIX 3.1 — a second run cannot take the advisory lock (real PostgreSQL) ────────
def test_pg_second_advisory_lock_blocks(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:                # unassigned price rows (valid FK, no product needed)
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        _use_pg(monkeypatch, e, Session)
        lock_conn = await e.connect()              # a real, separate connection holds the SAME lock
        got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:n,:o)"),
                                       {"n": obs._LOCK_NAMESPACE, "o": obs._LOCK_OPERATION})).scalar()
        assert got is True
        try:
            res = await obs.run_observation_retention(now=T0)
            async with Session() as db:
                cnt = (await db.execute(select(func.count()).select_from(MPO))).scalar_one()
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:n,:o)"),
                                    {"n": obs._LOCK_NAMESPACE, "o": obs._LOCK_OPERATION})
            await lock_conn.close()
        assert res.enabled is True and res.lock_acquired is False and res.price_removed == 0 and cnt == 2
        res2 = await obs.run_observation_retention(now=T0)     # lock now free -> cleans
        async with Session() as db:
            ids = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        await e.dispose()
        return res2, ids
    res2, ids = _run(_go())
    assert res2.lock_acquired is True and ids == {"p1"}


# ── FIX 3.2 — the advisory lock is released after a batch error (not stuck on the pool) ───────────────
def test_pg_advisory_lock_released_after_error(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", _boom_sessionmaker(Session))
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        errored = await obs.run_observation_retention(now=T0)     # got lock, batch failed, safe result
        # prove the lock is FREE now: a fresh connection can take it
        async with e.connect() as c:
            free = (await c.execute(text("SELECT pg_try_advisory_lock(:n,:o)"),
                                    {"n": obs._LOCK_NAMESPACE, "o": obs._LOCK_OPERATION})).scalar()
            if free:
                await c.execute(text("SELECT pg_advisory_unlock(:n,:o)"),
                                {"n": obs._LOCK_NAMESPACE, "o": obs._LOCK_OPERATION})
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)   # clear the fault
        good = await obs.run_observation_retention(now=T0)       # next run takes the lock and cleans
        async with Session() as db:
            ids = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        await e.dispose()
        return errored, free, good, ids
    errored, free, good, ids = _run(_go())
    assert errored.lock_acquired is True and errored.failed_batches == 1 and errored.price_removed == 0
    assert free is True                                          # not stuck on a pooled connection
    assert good.lock_acquired is True and ids == {"p1"}


# ── FIX 3.3 — real FK ON DELETE CASCADE removes children (no session_replication_role) ────────────────
def test_pg_parent_delete_cascades_children_real_fk(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:               # PARTIALLY_AUTO parents (unassigned) + mapped children
            for pid, fa in (("Q_old", old), ("Q_new", T0)):
                await db.execute(PO.__table__.insert().values(**_promo_row(
                    id=pid, marketplace_account_id="a", external_product_id="OF", promotion_id="PR",
                    provider_status="PARTIALLY_AUTO", attribution_status="exact_stores", fetched_at=fa)))
            for sid, pid, ca in (("SE_old", "Q_old", old), ("SE_new", "Q_new", T0)):
                await db.execute(SE.__table__.insert().values(
                    id=sid, promotion_observation_id=pid, marketplace_account_id="a",
                    external_store_id="c1", marketplace_store_id="st", mapping_status="mapped", created_at=ca))
            await db.commit()
        _use_pg(monkeypatch, e, Session)
        res = await obs.run_observation_retention(now=T0)
        async with Session() as db:
            parents = {r[0] for r in (await db.execute(select(PO.id))).all()}
            children = {r[0] for r in (await db.execute(select(SE.id))).all()}
        await e.dispose()
        return res, parents, children
    res, parents, children = _run(_go())
    assert res.promotion_removed == 1
    assert parents == {"Q_new"}                    # old non-latest parent gone
    assert children == {"SE_new"}                  # its child removed by real FK CASCADE, never directly


# ── FIX 3.4 — a batch error rolls back on real PostgreSQL; the next run continues ─────────────────────
def test_pg_rollback_keeps_rows_then_next_run_continues(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", _boom_sessionmaker(Session))
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        errored = await obs.run_observation_retention(now=T0)
        async with Session() as db:
            after_err = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)   # next run: fresh sessions, continues
        good = await obs.run_observation_retention(now=T0)
        async with Session() as db:
            after_good = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        await e.dispose()
        return errored, after_err, good, after_good
    errored, after_err, good, after_good = _run(_go())
    assert errored.failed_batches == 1 and after_err == {"p0", "p1"}   # rolled back — nothing deleted
    assert good.price_removed == 1 and after_good == {"p1"}            # next run cleans the old non-latest


# ── FIX 3 — SET LOCAL statement_timeout fires per batch → rollback (real PostgreSQL) ──────────────────
def test_pg_statement_timeout_rolls_back_batch(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = (T0 - timedelta(days=200)).isoformat(sep=" ")
        async with Session() as db:                # 5000 rows in one series -> a DELETE slow enough to trip 1ms
            await db.execute(text(
                "INSERT INTO marketplace_price_observations(id,ingest_run_id,marketplace_account_id,"
                "marketplace_store_id,product_id,external_product_id,resolution_status,observation_kind,"
                "promotion_key,currency_status,seller_revenue_status,commission_base_status,subsidy_status,"
                "source,fetched_at,last_verified_at,missing_fields,created_at)"
                # distinct params per column: fetched_at/created_at are timestamp, last_verified_at is
                # timestamptz -> one shared placeholder makes asyncpg deduce inconsistent types for $1.
                " SELECT 'p'||g,'r'||g,'a','st',NULL,'E','unassigned','catalog','__none__','unknown',"
                "'unknown','unknown','unknown','api',:fa,:lv,'[]',:cr FROM generate_series(1,5000) g"),
                {"fa": old, "lv": old, "cr": old})
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        monkeypatch.setattr(obs, "_STATEMENT_TIMEOUT", "1ms")     # SET LOCAL per batch -> DELETE times out
        res = await obs.run_observation_retention(now=T0, batch_size=5000)
        async with Session() as db:
            n = (await db.execute(select(func.count()).select_from(MPO))).scalar_one()
        await e.dispose()
        return res, n
    res, n = _run(_go())
    assert res.failed_batches >= 1 and res.price_removed == 0    # statement_timeout -> rolled back
    assert n == 5000                                             # every row intact (nothing committed)


# ── FIX 3b — SET LOCAL lock_timeout fires per batch when a row lock is held → rollback (real PostgreSQL) ──
def test_pg_lock_timeout_rolls_back_batch(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:                # p0 = old non-latest (the doomed row), p1 = latest (kept)
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        _use_pg(monkeypatch, e, Session)
        monkeypatch.setattr(obs, "_LOCK_TIMEOUT", "1ms")   # SET LOCAL per batch -> the DELETE cannot wait
        lock_conn = await e.connect()                       # a separate open txn holds a row lock on p0
        await lock_conn.execute(text("SELECT id FROM marketplace_price_observations "
                                     "WHERE id='p0' FOR UPDATE"))
        try:
            res = await obs.run_observation_retention(now=T0, batch_size=5000)   # DELETE p0 blocks -> lock_timeout
            async with Session() as db:
                n = (await db.execute(select(func.count()).select_from(MPO))).scalar_one()
        finally:
            await lock_conn.rollback()                      # release the held row lock
            await lock_conn.close()
        await e.dispose()
        return res, n
    res, n = _run(_go())
    assert res.failed_batches >= 1 and res.price_removed == 0    # lock_timeout -> rolled back, not deleted
    assert n == 2                                               # both rows intact (nothing committed)


# ── FIX 4 — the per-run deadline is checked BETWEEN batches → timed_out, next run continues ───────────
def test_pg_deadline_between_batches_timed_out(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        timed = await obs.run_observation_retention(now=T0, max_duration=0.0)     # deadline already passed
        async with Session() as db:
            after = (await db.execute(select(func.count()).select_from(MPO))).scalar_one()
        good = await obs.run_observation_retention(now=T0)                        # no deadline -> continues
        async with Session() as db:
            ids = {r[0] for r in (await db.execute(select(MPO.id))).all()}
        await e.dispose()
        return timed, after, good, ids
    timed, after, good, ids = _run(_go())
    assert timed.timed_out is True and timed.price_removed == 0 and after == 2   # stopped before any delete
    assert good.timed_out is False and good.price_removed == 1 and ids == {"p1"}  # next run cleans, latest kept


# ── scheduler dry-run vs real on real PostgreSQL ─────────────────────────────────
def test_pg_dry_run_counts_only(monkeypatch):
    e = _pg_engine_or_skip()
    Session = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)

    async def _go():
        await _pg_reset(e)
        await _pg_seed_valid(Session)
        old = T0 - timedelta(days=200)
        async with Session() as db:
            for i, fa in ((0, old), (1, T0)):
                await db.execute(MPO.__table__.insert().values(**_price_row(
                    id=f"p{i}", marketplace_account_id="a", marketplace_store_id="st",
                    external_product_id="E", resolution_status="unassigned", fetched_at=fa)))
            await db.commit()
        monkeypatch.setattr(obs, "engine", e)
        monkeypatch.setattr(obs, "AsyncSessionLocal", Session)
        monkeypatch.setattr(obs.settings, "observation_retention_enabled", True)
        res = await obs.run_observation_retention(now=T0, dry_run=True)
        async with Session() as db:
            n = (await db.execute(select(func.count()).select_from(MPO))).scalar_one()
        await e.dispose()
        return res, n
    res, n = _run(_go())
    assert res.dry_run and res.price_candidates == 1 and res.price_removed == 0 and n == 2  # counted, 0 deleted


# ══ existing functional DELETE (synthetic seed with FK bypass — kept as-is) ══════════════════════════
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
