"""
Sprint L1 — refuted loop foundation.

select_next_candidate is pure deterministic next-in-action-space. On REFUTED a
follow-up Decision is created (same insight/mp/sku/chain, next action_key,
step+1); confirmed/insufficient create nothing; the last alternative stops;
memory appends REFUTED + FOLLOWUP_CREATED; chains stay independent and the prior
Decision is never mutated.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.observation import Observation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow
from repositories import decision_outcome as outcome_repo
from services import refuted_loop
from services.refuted_loop import select_next_candidate, create_followup_for_refuted
from services.marketplace import credential_vault, metric_reader
from services.marketplace.metric_reader import MetricSample
from services.measurement_close_bridge import close_due_measurements

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _decision(db, uid, action_key, *, chain="c1", step=0, sku="SKU1"):
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key=action_key, insight_key=f"margin_crisis:wb:{sku}",
                 physical_product_id="phys-1", decision_chain_id=chain, step_in_chain=step)
    db.add(d); await db.flush()
    return d


# ── pure selection ───────────────────────────────────────────────────────────

def test_select_next_sequence():
    assert select_next_candidate("margin_crisis", "set_price") == "reduce_discount"
    assert select_next_candidate("margin_crisis", "reduce_discount") == "stop_auto_promotion"
    assert select_next_candidate("margin_crisis", "stop_auto_promotion") is None  # last → stop
    assert select_next_candidate("margin_crisis", "unknown_action") is None
    assert select_next_candidate("pricing_problem", "set_price") is None  # no action space


# ── follow-up creation ───────────────────────────────────────────────────────

def test_followup_created_same_chain_next_step():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "set_price", chain="chainX", step=0)
        f = await create_followup_for_refuted(db, d)
        assert f is not None
        assert f.action_key == "reduce_discount"
        assert f.decision_chain_id == "chainX"        # SAME chain
        assert f.step_in_chain == 1                    # next step
        assert f.insight_key == d.insight_key and f.physical_product_id == d.physical_product_id
        assert f.id != d.id and f.source == "followup"
        # prior decision not mutated
        assert d.action_key == "set_price" and d.step_in_chain == 0
    _run(go())


def test_followup_reduce_discount_to_stop_promo():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "reduce_discount", step=1)
        f = await create_followup_for_refuted(db, d)
        assert f.action_key == "stop_auto_promotion" and f.step_in_chain == 2
    _run(go())


def test_last_alternative_no_followup():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "stop_auto_promotion", step=2)
        assert await create_followup_for_refuted(db, d) is None
    _run(go())


def test_followup_idempotent_when_alternative_exists():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, "set_price", chain="A", step=0)
        # reduce_discount already exists (e.g. pre-promoted alternative)
        await _decision(db, uid, "reduce_discount", chain="B", step=0)
        assert await create_followup_for_refuted(db, d) is None
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert sum(1 for r in rows if r.action_key == "reduce_discount") == 1  # no dup
    _run(go())


# ── integration through measurement close ────────────────────────────────────

async def _seed_refutable(db, uid, sku, baseline_profit, realized_profit):
    # Baseline is the manual Observation below; the realized read sums finance in
    # the close window. Only the realized row is in-window so the read == realized.
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                              date="2026-06-19", sku=sku, net_profit=realized_profit))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                 status="connected", scopes=["prices"])
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("t")))
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key="set_price", insight_key=f"margin_crisis:wb:{sku}",
                 physical_product_id="phys-1", decision_chain_id="ch1", step_in_chain=0)
    db.add(d)
    base = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                       entity_id=sku, metric_name="net_profit", marketplace="wb",
                       value=baseline_profit, unit="rub", observed_at=PAST, source="compute")
    db.add(base); await db.flush()
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=d.id, metric_name="net_profit", expected_window_days=7,
        baseline_observation_id=base.id)
    out.created_at = PAST
    await db.commit()
    return d


def test_refuted_close_creates_followup_and_memory():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # realized net_profit (100) < baseline (500) → refuted
        d = await _seed_refutable(db, uid, "SKU1", baseline_profit=500.0, realized_profit=100.0)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        # original + follow-up reduce_discount
        assert {r.action_key for r in rows} == {"set_price", "reduce_discount"}
        f = next(r for r in rows if r.action_key == "reduce_discount")
        assert f.decision_chain_id == "ch1" and f.step_in_chain == 1
        # memory: REFUTED + FOLLOWUP_CREATED
        mem = (await db.execute(select(DecisionMemory.outcome))).scalars().all()
        assert "refuted" in mem and "followup_created" in mem
    _run(go())


def test_confirmed_close_creates_no_followup():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # realized (900) > baseline (500) → confirmed
        await _seed_refutable(db, uid, "SKU2", baseline_profit=500.0, realized_profit=900.0)
        s = await close_due_measurements(db, now=NOW)
        assert s.confirmed == 1
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert [r.action_key for r in rows] == ["set_price"]  # no follow-up
        mem = (await db.execute(select(DecisionMemory.outcome))).scalars().all()
        assert "followup_created" not in mem
    _run(go())


# ── guard: pure selection, no learning ───────────────────────────────────────

def test_selection_has_no_learning():
    import inspect, ast
    tree = ast.parse(inspect.getsource(refuted_loop))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("sklearn", "numpy", "torch", "decision_candidate_engine",
                "decision_policy_engine"):
        assert all(bad not in m for m in mods)
    src = inspect.getsource(select_next_candidate)
    for bad in ("score", "weight", "confidence_rate", "random"):
        assert bad not in src
