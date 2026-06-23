"""
Promotion Activation A4 — advertising audit hook tests.

After a successful advertising audit, run_promotion fires as a NON-BLOCKING
side-effect: actionable signals become Decisions and appear in the feed. A promotion
failure never breaks the audit. No executor/apply/measurement/marketplace. Only the
advertising router carries the hook.
"""
import asyncio
import inspect
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.decision import Decision
from models.advertising_signal import AdvertisingSignal
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.execution_log import ExecutionLog

import routers.advertising_engine as adv_eng
from routers.advertising_engine import run_advertising_audit, AdvAuditRequest, ThresholdsIn, AdvAuditResponse
from services.decision_feed.builder import build_feed

TH = ThresholdsIn(max_drr=20.0, min_revenue_for_signal=1000.0, min_ad_spend_for_signal=100.0,
                  low_margin_threshold=10.0, low_stock_units=5, oos_risk_days=7.0)


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


async def _seed_finance(db, uid, *, mp="wb", sku="SKU1", net_profit=-500.0, ad_spend=4000.0):
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=mp, sku=sku,
                              revenue=10000.0, net_profit=net_profit, ad_spend=ad_spend, quantity=20))
    await db.flush()


async def _audit(db, uid, *, mp="wb", sku="SKU1"):
    return await run_advertising_audit(
        AdvAuditRequest(listing_id="L1", marketplace=mp, sku=sku, thresholds=TH),
        current_user=_User(uid), db=db)


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1. actionable signal → audit hook promotes to Decision, feed shows it ────

def test_audit_hook_promotes_actionable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        resp = await _audit(db, uid)
        assert isinstance(resp, AdvAuditResponse) and resp.ok
        assert resp.promotion is not None and resp.promotion.status == "completed"
        assert resp.promotion.decisions_created >= 1
        sig = (await db.execute(select(AdvertisingSignal).where(
            AdvertisingSignal.problem_type == "ad_destroying_profit"))).scalars().one()
        assert sig.status == "promoted_to_decision" and sig.decision_id
        d = (await db.execute(select(Decision).where(Decision.id == sig.decision_id))).scalars().one()
        assert d.action_key == "stop_auto_promotion"
        feed = await build_feed(db, user_id=uid)
        promoted = [i for i in feed if i.source_context.get("decision_id") == sig.decision_id]
        assert promoted and promoted[0].source_status == "promoted_to_decision"
    _run(go())


# ── 2. non-actionable audit → promotion runs, no Decision, audit ok ──────────

def test_audit_hook_non_actionable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid, net_profit=2000.0, ad_spend=0.0)  # profitable, no ad bleed
        await db.commit()
        resp = await _audit(db, uid)
        assert resp.ok and resp.promotion is not None
        assert resp.promotion.status == "completed" and resp.promotion.decisions_created == 0
        assert await _count(db, Decision) == 0
    _run(go())


# ── 3. promotion failure does not break the audit ───────────────────────────

def test_promotion_failure_non_blocking():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        async def boom(*a, **k):
            raise RuntimeError("promotion blew up")
        orig = adv_eng.run_promotion
        adv_eng.run_promotion = boom
        try:
            resp = await _audit(db, uid)
        finally:
            adv_eng.run_promotion = orig
        assert resp.ok and resp.status == "completed"          # audit still succeeds
        assert resp.promotion is not None and resp.promotion.status == "failed"
        assert resp.audit_id is not None                       # audit persisted
    _run(go())


# ── 4. hook does not execute / apply / measure / call marketplace ────────────

def test_hook_no_execution_no_measurement():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        await _audit(db, uid)
        assert await _count(db, ExecutionLog) == 0 and await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 5. idempotency: a second audit does not duplicate the Decision/link ──────

def test_audit_hook_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_finance(db, uid); await db.commit()
        r1 = await _audit(db, uid)
        r2 = await _audit(db, uid)
        assert r1.promotion.decisions_created >= 1 and r2.promotion.decisions_created == 0
        n1 = await _count(db, Decision)
        assert n1 == r1.promotion.decisions_created   # stable across the re-run
        assert await _count(db, EngineSignalDecisionLink) == n1
    _run(go())


# ── 6. only advertising carries the hook (others untouched) ──────────────────

def test_only_advertising_has_hook():
    import routers.seo as seo_r
    import routers.review_engine as rev_r
    import routers.growth_engine as gro_r
    import routers.legal_engine as leg_r
    for mod in (seo_r, rev_r, gro_r, leg_r):
        assert "run_promotion" not in inspect.getsource(mod), mod.__name__
    # advertising DOES carry it
    assert "run_promotion" in inspect.getsource(adv_eng)
