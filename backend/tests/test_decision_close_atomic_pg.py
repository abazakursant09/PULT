"""SECURITY-2D-2-C (DEC-2) — atomic decision-outcome close on REAL PostgreSQL 16.

Proves exactly one close winner per DecisionOutcome under true cross-connection concurrency: concurrent
close passes serialize on the outcome row lock, the loser sees the FRESH terminal label (populate_existing
defeats the stale identity-map copy that select_due_outcomes cached) and skips before inserting a realized
Observation or a DecisionMemory row — so an outcome is learned exactly once and outcome ranking cannot be
double-weighted. Independent AsyncSessions + real asyncio.gather; skipped locally, runs in postgres-explain
CI (0 skip).
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.observation import Observation
from models.imported_finance import ImportedFinanceRow
from models.user import User
from repositories import decision_outcome as outcome_repo
import services.measurement_close_bridge as bridge
from services.measurement_close_bridge import close_due_measurements

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)
SKU = "SKU1"


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
_TABLES = ("decision_memory", "decision_outcomes", "observations", "imported_finance_rows",
           "decisions", "users")


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


async def _seed(Session, *, uid=None, net_profit=900.0, baseline=500.0):
    """A net_profit (compute) still_open outcome that closes CONFIRMED from local finance — no token."""
    uid = uid or str(uuid.uuid4())
    did = str(uuid.uuid4())
    async with Session() as s:
        s.add(User(id=uid, email=f"{uid}@t.test", name="T", hashed_password="x"))
        await s.flush()
        s.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                 date="2026-06-19", sku=SKU, net_profit=net_profit))
        s.add(Decision(id=did, user_id=uid, problem="p", status="open", action_key="set_price",
                       insight_key=f"margin_crisis:wb:{SKU}", physical_product_id="phys-1",
                       decision_chain_id="ch1", step_in_chain=0))
        base = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing", entity_id=SKU,
                           metric_name="net_profit", marketplace="wb", value=baseline, unit="rub",
                           observed_at=PAST, source="compute")
        s.add(base)
        await s.flush()
        out = await outcome_repo.create_still_open_outcome(
            s, decision_id=did, metric_name="net_profit", expected_window_days=7,
            baseline_observation_id=base.id)
        out.created_at = PAST
        await s.commit()
    return uid, did


async def _close(Session):
    async with Session() as s:
        return await close_due_measurements(s, now=NOW)


async def _obs_count(Session, uid):
    async with Session() as s:
        return (await s.execute(select(func.count()).select_from(Observation)
                                .where(Observation.user_id == uid))).scalar_one()


async def _mem_count(Session, did, outcome="confirmed"):
    async with Session() as s:
        return (await s.execute(select(func.count()).select_from(DecisionMemory)
                                .where(DecisionMemory.decision_id == did,
                                       DecisionMemory.outcome == outcome))).scalar_one()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. normal close ───────────────────────────────────────────────────────────────

def test_pg_normal_close(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            s = await _close(S)
            assert s.confirmed == 1
            assert await _obs_count(S, uid) == 2               # baseline + one realized
            assert await _mem_count(S, did) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 2/3. concurrent close → exactly one winner ─────────────────────────────────────

def test_pg_concurrent_two_one_winner(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            res = await asyncio.gather(_close(S), _close(S), return_exceptions=True)
            assert all(not isinstance(r, Exception) for r in res)     # no 500
            assert sum(r.confirmed for r in res) == 1                 # exactly one closed
            assert await _obs_count(S, uid) == 2                      # one realized observation
            assert await _mem_count(S, did) == 1                      # one memory → ranking not doubled
        finally:
            await eng.dispose()
    _run(go())


def test_pg_concurrent_ten_one_winner(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            res = await asyncio.gather(*[_close(S) for _ in range(10)], return_exceptions=True)
            assert all(not isinstance(r, Exception) for r in res)
            assert sum(r.confirmed for r in res) == 1
            assert await _obs_count(S, uid) == 2
            assert await _mem_count(S, did) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 5. STALE identity-map regression (MANDATORY) ──────────────────────────────────

def test_pg_stale_identity_map_loser_sees_terminal(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            async with S() as loser, S() as winner:
                # loser caches the row as still_open in ITS identity map (as select_due_outcomes would)
                pre = await outcome_repo.get_by_decision_id(loser, did)
                assert pre.outcome_label == "still_open"
                # winner closes and commits on its own session
                w = await close_due_measurements(winner, now=NOW)
                assert w.confirmed == 1
                # loser now takes the lock + fresh load: MUST observe the terminal label, not the cached
                # still_open. This assertion fails if populate_existing is removed from the locked fetch.
                fresh = await outcome_repo.get_by_decision_id_for_update(loser, did)
                assert fresh.outcome_label != "still_open"
                await loser.rollback()
            assert await _obs_count(S, uid) == 2                     # only the winner's realized obs
            assert await _mem_count(S, did) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 6. already terminal → skip ────────────────────────────────────────────────────

def test_pg_sequential_repeat_skips(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            await _close(S)
            s2 = await _close(S)
            assert s2.confirmed == 0
            assert await _obs_count(S, uid) == 2
            assert await _mem_count(S, did) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 7. failure before commit → still_open, nothing persisted, retry closes once ────

def test_pg_failure_before_commit_then_retry(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid, did = await _seed(S)
            # inject a failure inside the close (after the lock, before commit)
            orig = bridge.close_measurement

            async def _boom(*a, **k):
                raise RuntimeError("injected pre-commit failure")
            monkeypatch.setattr(bridge, "close_measurement", _boom)
            s1 = await _close(S)
            assert s1.errors == 1 and s1.confirmed == 0
            async with S() as s:
                assert (await outcome_repo.get_by_decision_id(s, did)).outcome_label == "still_open"
            assert await _obs_count(S, uid) == 1                     # baseline only, no realized
            assert await _mem_count(S, did) == 0                     # no memory written on failure
            # retry without the fault → closes exactly once
            monkeypatch.setattr(bridge, "close_measurement", orig)
            s2 = await _close(S)
            assert s2.confirmed == 1
            assert await _obs_count(S, uid) == 2
            assert await _mem_count(S, did) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 12. different decisions close independently ────────────────────────────────────

def test_pg_different_decisions_independent(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            uid1, d1 = await _seed(S)
            uid2, d2 = await _seed(S)
            res = await asyncio.gather(_close(S), _close(S), return_exceptions=True)
            assert all(not isinstance(r, Exception) for r in res)
            # both decisions close (across the two passes) — total confirmed across passes == 2
            assert sum(r.confirmed for r in res) == 2
            assert await _mem_count(S, d1) == 1
            assert await _mem_count(S, d2) == 1
        finally:
            await eng.dispose()
    _run(go())
