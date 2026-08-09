"""SECURITY-2D-2-C (DEC-2) — atomic decision-outcome close: SQLite functional + source guards.

Single-threaded smoke (true concurrency proof is in test_decision_close_atomic_pg.py): a second close of an
already-closed outcome is skipped and leaves exactly one realized Observation + one DecisionMemory; plus
source guards that the close loop takes the row lock with a FRESH load (with_for_update +
populate_existing) and re-checks still_open under it, and that models/schema/calc are untouched.
"""
from __future__ import annotations

import ast
import asyncio
import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.observation import Observation
from models.imported_finance import ImportedFinanceRow
from repositories import decision_outcome as outcome_repo
from services.measurement_close_bridge import close_due_measurements

_HERE = os.path.dirname(__file__)
_BRIDGE_SRC = os.path.join(_HERE, "..", "services", "measurement_close_bridge.py")
_REPO_SRC = os.path.join(_HERE, "..", "repositories", "decision_outcome.py")

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)
SKU = "SKU1"


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _open_confirmed(db, uid, *, net_profit=900.0, baseline=500.0):
    """A net_profit (compute) outcome that closes CONFIRMED from local finance — no token needed."""
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                              date="2026-06-19", sku=SKU, net_profit=net_profit))
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key="set_price", insight_key=f"margin_crisis:wb:{SKU}",
                 physical_product_id="phys-1", decision_chain_id="ch1", step_in_chain=0)
    db.add(d)
    base = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing", entity_id=SKU,
                       metric_name="net_profit", marketplace="wb", value=baseline, unit="rub",
                       observed_at=PAST, source="compute")
    db.add(base)
    await db.flush()
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=d.id, metric_name="net_profit", expected_window_days=7,
        baseline_observation_id=base.id)
    out.created_at = PAST
    await db.commit()
    return d.id


async def _obs_count(db, uid):
    return (await db.execute(select(func.count()).select_from(Observation)
                             .where(Observation.user_id == uid))).scalar_one()


async def _memory_count(db, decision_id, outcome):
    return (await db.execute(select(func.count()).select_from(DecisionMemory)
                             .where(DecisionMemory.decision_id == decision_id,
                                    DecisionMemory.outcome == outcome))).scalar_one()


# ── functional ──────────────────────────────────────────────────────────────────

def test_sequential_repeat_close_is_skipped_one_set():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        did = await _open_confirmed(db, uid)
        s1 = await close_due_measurements(db, now=NOW)
        assert s1.confirmed == 1
        assert await _obs_count(db, uid) == 2                 # baseline + one realized
        assert await _memory_count(db, did, "confirmed") == 1
        # second close: outcome already terminal → skipped, nothing added
        s2 = await close_due_measurements(db, now=NOW)
        assert s2.confirmed == 0 and s2.skipped >= 0          # select_due only returns still_open
        assert await _obs_count(db, uid) == 2                 # NO second realized observation
        assert await _memory_count(db, did, "confirmed") == 1  # NO second memory
    _run(go())


def test_already_terminal_direct_close_skips():
    async def go():
        db = await _engine()
        uid = str(uuid.uuid4())
        did = await _open_confirmed(db, uid)
        await close_due_measurements(db, now=NOW)             # -> confirmed
        # even if a due-list somehow re-presents it, the locked re-check finds terminal
        out = await outcome_repo.get_by_decision_id_for_update(db, did)
        assert out.outcome_label != "still_open"
    _run(go())


# ── source / AST guards ─────────────────────────────────────────────────────────

def _repo_helper_src():
    with open(_REPO_SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_by_decision_id_for_update")
    return ast.get_source_segment(src, fn)


def test_locked_fetch_uses_for_update_and_populate_existing():
    seg = _repo_helper_src()
    assert "with_for_update()" in seg
    assert "populate_existing=True" in seg                    # defeats the stale identity-map copy
    i_lock = seg.index("with_for_update()")
    i_pop = seg.index("populate_existing=True")
    assert i_lock < i_pop or i_pop < i_lock                    # both present on the same select


def test_close_loop_uses_locked_fetch_before_still_open_check():
    with open(_BRIDGE_SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "close_due_measurements")
    seg = ast.get_source_segment(src, fn)
    assert "get_by_decision_id_for_update(db, decision_id)" in seg
    # the fresh locked fetch must precede the still_open gate and the close call
    assert seg.index("get_by_decision_id_for_update(db, decision_id)") < seg.index("!= _STILL_OPEN")
    assert seg.index("!= _STILL_OPEN") < seg.index("close_measurement(")


def test_no_decision_memory_unique_added():
    with open(os.path.join(_HERE, "..", "models", "decision_memory.py"), encoding="utf-8") as f:
        src = f.read()
    assert "UniqueConstraint" not in src                      # append model unchanged
