"""
Sprint G1 — runtime measurement-close trigger.

Proves the new POST /api/decisions/measurements/close-due endpoint activates the
Learning OS write-side end to end: a promoted decision with an open net_profit
measurement is closed THROUGH THE ENDPOINT (not by calling the service directly),
which records a DecisionMemory row and opens a refuted follow-up. Re-invoking the
endpoint closes nothing (idempotent — no duplicate closes). Reuses the existing
close bridge / outcome repo seeding; no new business logic.
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
from models.decision_memory import DecisionMemory
from models.observation import Observation
from models.imported_finance import ImportedFinanceRow
from repositories import decision_outcome as outcome_repo
from services.insight_decision_bridge import promote_insight_to_decision, InsightPromotionDTO

import pytest
from fastapi import HTTPException

from config import settings
from routers.decisions import close_due_measurements_endpoint, CloseDueResponse

SKU = "SKU1"
IKEY = f"margin_crisis:wb:{SKU}"
KEY = "test-internal-key"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _open_net_profit_outcome(db, decision, *, now, baseline=500.0):
    """Open a still_open net_profit outcome with a baseline fact, dated in the past
    so it is already due relative to `now`."""
    past = now - timedelta(days=30)
    base = Observation(id=str(uuid.uuid4()), user_id=decision.user_id, entity_grain="listing",
                       entity_id=SKU, metric_name="net_profit", marketplace="wb",
                       value=baseline, unit="rub", observed_at=past, source="compute")
    db.add(base); await db.flush()
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=decision.id, metric_name="net_profit", expected_window_days=7,
        baseline_observation_id=base.id)
    out.created_at = past
    await db.commit()


async def _call_endpoint(db, *, key=KEY):
    """Invoke the runtime trigger with the internal-key header value."""
    return await close_due_measurements_endpoint(limit=None, x_internal_key=key, db=db)


def test_endpoint_closes_records_memory_and_opens_followup():
    async def go():
        settings.internal_api_key = KEY
        db = await _engine(); uid = str(uuid.uuid4())
        now = datetime.utcnow()
        # realized net_profit 100 < baseline 500 → close refutes
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date=now.strftime("%Y-%m-%d"), sku=SKU, net_profit=100.0))
        await db.flush()

        # promote first margin decision + open its measurement
        await promote_insight_to_decision(
            db, user_id=uid, insight=InsightPromotionDTO(
                insight_key=IKEY, itype="margin_crisis", marketplace="wb", sku=SKU, problem="p"))
        await db.commit()
        d0 = (await db.execute(select(Decision).where(
            Decision.user_id == uid, Decision.action_key == "set_price"))).scalar_one()
        chain = d0.decision_chain_id
        await _open_net_profit_outcome(db, d0, now=now)

        # ── runtime trigger: the ENDPOINT closes the due measurement ──────────
        resp = await _call_endpoint(db)
        assert isinstance(resp, CloseDueResponse)
        assert resp.total_due == 1
        assert resp.refuted == 1

        # DecisionMemory row appended (write-side activated)
        mem = (await db.execute(select(DecisionMemory).where(
            DecisionMemory.decision_id == d0.id))).scalars().all()
        assert len(mem) == 1 and mem[0].outcome == "refuted"

        # refuted → follow-up decision created in the same chain, step+1
        decisions = (await db.execute(select(Decision).where(
            Decision.user_id == uid))).scalars().all()
        assert len(decisions) == 2
        follow = next(d for d in decisions if d.id != d0.id)
        assert follow.decision_chain_id == chain
        assert follow.step_in_chain == 1
        assert follow.action_key == "reduce_discount"  # next in margin action space
        assert follow.status == "open"                 # NOT auto-executed

        # ── idempotent: second trigger closes nothing (no duplicate close) ────
        resp2 = await _call_endpoint(db)
        assert resp2.total_due == 0 and resp2.refuted == 0
        mem2 = (await db.execute(select(DecisionMemory).where(
            DecisionMemory.decision_id == d0.id))).scalars().all()
        assert len(mem2) == 1  # no duplicate memory row
    _run(go())


def test_endpoint_noop_when_nothing_due():
    async def go():
        settings.internal_api_key = KEY
        db = await _engine()
        resp = await _call_endpoint(db)
        assert resp.total_due == 0
        assert resp.confirmed == 0 and resp.refuted == 0 and resp.errors == 0
    _run(go())


# ── G1.1 internal-key gate ───────────────────────────────────────────────────

def test_rejects_without_key():
    async def go():
        settings.internal_api_key = KEY
        db = await _engine()
        with pytest.raises(HTTPException) as ei:
            await _call_endpoint(db, key=None)        # ordinary caller, no header
        assert ei.value.status_code == 403
    _run(go())


def test_rejects_wrong_key():
    async def go():
        settings.internal_api_key = KEY
        db = await _engine()
        with pytest.raises(HTTPException) as ei:
            await _call_endpoint(db, key="not-the-key")
        assert ei.value.status_code == 403
    _run(go())


def test_fail_closed_when_secret_unset():
    async def go():
        settings.internal_api_key = ""               # operator never configured it
        db = await _engine()
        with pytest.raises(HTTPException) as ei:
            await _call_endpoint(db, key="anything")
        assert ei.value.status_code == 403
    _run(go())
