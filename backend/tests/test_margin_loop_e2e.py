"""
Margin loop end-to-end audit (post d4db9a5 + 03662b2).

Drives the automated closed loop for one margin_crisis insight:
  promote set_price → open net_profit measurement → close REFUTED →
  follow-up reduce_discount (same chain, step+1) → open → close REFUTED →
  follow-up stop_auto_promotion (step+2) → open → close REFUTED →
  action space exhausted → NO follow-up (chain stops).
Asserts chain integrity, step ordering, memory append, and idempotency.
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
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.marketplace import credential_vault
from repositories import decision_outcome as outcome_repo
from services.insight_decision_bridge import promote_insight_to_decision, InsightPromotionDTO
from services.measurement_close_bridge import close_due_measurements

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)
SKU = "SKU1"
IKEY = f"margin_crisis:wb:{SKU}"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _open_net_profit_outcome(db, decision, baseline=500.0):
    """Simulate apply: open a still_open net_profit outcome with a baseline fact."""
    base = Observation(id=str(uuid.uuid4()), user_id=decision.user_id, entity_grain="listing",
                       entity_id=SKU, metric_name="net_profit", marketplace="wb",
                       value=baseline, unit="rub", observed_at=PAST, source="compute")
    db.add(base); await db.flush()
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=decision.id, metric_name="net_profit", expected_window_days=7,
        baseline_observation_id=base.id)
    out.created_at = PAST
    await db.commit()


async def _decision_by_action(db, uid, action_key):
    return (await db.execute(select(Decision).where(
        Decision.user_id == uid, Decision.action_key == action_key))).scalar_one()


async def _seed_credentials(db, uid):
    """A connected WB cabinet with prices + promotions scopes (token resolves)."""
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                 status="connected", scopes=["prices", "promotions"])
    db.add(conn)
    for scope in ("prices", "promotions"):
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                             secret_enc=credential_vault.encrypt("t")))
    await db.flush()


def test_margin_loop_closes_and_stops():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # realized net_profit always 100 < baseline 500 → every close refutes
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date="2026-06-19", sku=SKU, net_profit=100.0))
        await db.flush()
        await _seed_credentials(db, uid)

        # 1) promote the first margin decision (single → set_price, legacy mapping)
        r = await promote_insight_to_decision(
            db, user_id=uid, insight=InsightPromotionDTO(
                insight_key=IKEY, itype="margin_crisis", marketplace="wb", sku=SKU, problem="p"))
        await db.commit()
        d0 = await _decision_by_action(db, uid, "set_price")
        chain = d0.decision_chain_id
        assert d0.step_in_chain == 0

        # 2) apply (open measurement) → close REFUTED → follow-up reduce_discount
        await _open_net_profit_outcome(db, d0)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1
        d1 = await _decision_by_action(db, uid, "reduce_discount")
        assert d1.decision_chain_id == chain and d1.step_in_chain == 1

        # 3) apply reduce_discount → close REFUTED → follow-up stop_auto_promotion
        await _open_net_profit_outcome(db, d1)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1
        d2 = await _decision_by_action(db, uid, "stop_auto_promotion")
        assert d2.decision_chain_id == chain and d2.step_in_chain == 2

        # 4) apply stop_auto_promotion → close REFUTED → action space exhausted, NO follow-up
        await _open_net_profit_outcome(db, d2)
        s = await close_due_measurements(db, now=NOW)
        assert s.refuted == 1

        # ── chain integrity ───────────────────────────────────────────────────
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert len(rows) == 3                                   # no 4th decision
        assert {r.action_key for r in rows} == {"set_price", "reduce_discount", "stop_auto_promotion"}
        assert {r.decision_chain_id for r in rows} == {chain}   # one chain
        assert sorted(r.step_in_chain for r in rows) == [0, 1, 2]

        # ── memory append: 3 REFUTED + 2 FOLLOWUP_CREATED ─────────────────────
        mem = (await db.execute(select(DecisionMemory.outcome))).scalars().all()
        assert mem.count("refuted") == 3
        assert mem.count("followup_created") == 2

        # ── idempotency: re-run close creates nothing new ─────────────────────
        s2 = await close_due_measurements(db, now=NOW)
        assert s2.total_due == 0
        rows2 = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert len(rows2) == 3
        mem2 = (await db.execute(select(DecisionMemory.outcome))).scalars().all()
        assert mem2.count("refuted") == 3 and mem2.count("followup_created") == 2
    _run(go())


def test_confirmed_branch_stops_chain():
    """A confirmed close ends the chain — no follow-up."""
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date="2026-06-19", sku=SKU, net_profit=900.0))  # > baseline → confirmed
        await db.flush()
        await _seed_credentials(db, uid)
        r = await promote_insight_to_decision(
            db, user_id=uid, insight=InsightPromotionDTO(
                insight_key=IKEY, itype="margin_crisis", marketplace="wb", sku=SKU, problem="p"))
        await db.commit()
        d0 = await _decision_by_action(db, uid, "set_price")
        await _open_net_profit_outcome(db, d0)
        s = await close_due_measurements(db, now=NOW)
        assert s.confirmed == 1
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert [r.action_key for r in rows] == ["set_price"]   # chain ended, no follow-up
        mem = (await db.execute(select(DecisionMemory.outcome))).scalars().all()
        assert mem == ["confirmed"]
    _run(go())
