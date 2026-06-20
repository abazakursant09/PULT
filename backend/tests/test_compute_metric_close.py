"""
Gap A fix — compute metrics close without a marketplace token.

net_profit (compute) closes from local finance even with NO marketplace
credential, and its refuted follow-up still fires. API metrics (revenue) keep
the token gate: no token → skipped, outcome stays still_open.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.observation import Observation
from models.imported_finance import ImportedFinanceRow
from repositories import decision_outcome as outcome_repo
from services.measurement_close_bridge import close_due_measurements

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)
SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _open(db, uid, metric, baseline, action_key="set_price"):
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key=action_key, insight_key=f"margin_crisis:wb:{SKU}",
                 physical_product_id="phys-1", decision_chain_id="ch1", step_in_chain=0)
    db.add(d)
    base = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                       entity_id=SKU, metric_name=metric, marketplace="wb",
                       value=baseline, unit="rub", observed_at=PAST, source="compute")
    db.add(base); await db.flush()
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=d.id, metric_name=metric, expected_window_days=7,
        baseline_observation_id=base.id)
    out.created_at = PAST
    await db.commit()
    return d, out


# ── compute metric closes with NO credential ─────────────────────────────────

def test_net_profit_closes_without_credential():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # realized net_profit 100 < baseline 500 → refuted. NO connection/credential.
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date="2026-06-19", sku=SKU, net_profit=100.0))
        await db.flush()
        d, _ = await _open(db, uid, "net_profit", 500.0)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1 and s.skipped == 0      # closed, not stalled
        # refuted follow-up still fires (compute close → next alternative)
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert {r.action_key for r in rows} == {"set_price", "reduce_discount"}
    _run(go())


def test_net_profit_no_finance_insufficient_without_credential():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # compute metric, baseline present, NO finance rows in window, NO credential
        d, _ = await _open(db, uid, "net_profit", 500.0)
        s = await close_due_measurements(db, now=NOW)
        assert s.insufficient == 1 and s.skipped == 0   # honest insufficient, not skipped
    _run(go())


# ── API metric keeps the token gate ──────────────────────────────────────────

def test_api_metric_without_token_stays_open():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # revenue is an API metric → needs a token. No credential → skipped, still_open.
        d, _ = await _open(db, uid, "revenue", 100.0)
        s = await close_due_measurements(db, now=NOW)
        assert s.skipped == 1 and s.refuted == 0 and s.confirmed == 0
        row = await outcome_repo.get_by_decision_id(db, d.id)
        assert row.outcome_label == "still_open"        # preserved for a later attempt
    _run(go())
