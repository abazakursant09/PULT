"""
Promotion Activation A2 — runner + ledger tests.

run_promotion turns ON the existing Decision Outcome promotion/bridge: eligible
actionable advertising signals become Decisions (capability-gated); advice-only
contours do not; Yandex is capability-skipped; re-runs are idempotent. Decision is
an intent record — no execution, no measurement, no marketplace. promotion_run is an
append-only ledger.
"""
import asyncio
import uuid
from dataclasses import fields as dc_fields
from datetime import datetime

from sqlalchemy import select, func, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.seo_signal import SeoSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.execution_log import ExecutionLog
from models.promotion_run import PromotionRun

from services.promotion_activation.runner import run_promotion, PromotionRunResult
from services.decision_feed.builder import build_feed

T0 = datetime(2026, 6, 22)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _adv(db, uid, *, mp="wildberries", sku="SKU1", itype="ad_on_low_stock"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key=f"adv_{itype}", problem_type=itype,
           insight_key=f"adv_{itype}:{mp}:{sku}", marketplace=mp, sku=sku, status="active",
           what="x", why="y", expected_effect="z", what_to_do="w", priority_level="high"))
    await db.commit()


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1. actionable advertising → link + Decision + signal promoted ────────────

def test_actionable_adv_promotes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        res = await run_promotion(db, user_id=uid, now=T0); await db.commit()
        assert isinstance(res, PromotionRunResult)
        assert res.links_created == 1 and res.decisions_created == 1
        link = (await db.execute(select(EngineSignalDecisionLink))).scalars().one()
        assert link.decision_id is not None and link.link_status == "promoted"
        d = (await db.execute(select(Decision))).scalars().one()
        assert d.action_key == "stop_auto_promotion"
        sig = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig.status == "promoted_to_decision" and sig.decision_id == d.id
        # no execution, no measurement — Decision is intent only
        assert await _count(db, ExecutionLog) == 0 and await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 2/3/4. SEO / Growth / Legal → no Decision ────────────────────────────────

def test_advice_only_contours_no_decision():
    async def go():
        for model, sk, pt, ik in (
            (SeoSignal, "seo_title_too_short", "title_too_short", "seo_title_too_short:wb:SKU1"),
            (GrowthSignal, "growth_margin_expansion_candidate", "margin_expansion_candidate",
             "growth_margin_expansion_candidate:wb:SKU1"),
            (LegalSignal, "legal_content_claim_risk", "content_claim_risk",
             "legal_content_claim_risk:wb:SKU1"),
        ):
            db = await _engine(); uid = str(uuid.uuid4())
            kw = dict(audit_id=str(uuid.uuid4()), user_id=uid, signal_key=sk,
                      insight_key=ik, marketplace="wb", sku="SKU1", status="active")
            if model is LegalSignal:
                kw["requirement_type"] = pt
            else:
                kw["problem_type"] = pt
            db.add(model(**kw)); await db.commit()
            res = await run_promotion(db, user_id=uid, now=T0); await db.commit()
            assert res.decisions_created == 0 and res.links_created == 0
            assert await _count(db, Decision) == 0
    _run(go())


# ── 5. Yandex actionable → capability skip (no Decision) ─────────────────────

def test_yandex_capability_skip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, mp="yandex", sku="SKU9")
        res = await run_promotion(db, user_id=uid, now=T0); await db.commit()
        assert res.links_created == 1 and res.decisions_created == 0   # link made, bridge skipped
        assert await _count(db, Decision) == 0
        link = (await db.execute(select(EngineSignalDecisionLink))).scalars().one()
        assert link.decision_id is None and link.link_status == "proposed"
    _run(go())


# ── 6. repeat run → no duplicate Decision/link ───────────────────────────────

def test_repeat_run_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        r1 = await run_promotion(db, user_id=uid, now=T0); await db.commit()
        r2 = await run_promotion(db, user_id=uid, now=T0); await db.commit()
        assert r1.decisions_created == 1 and r2.decisions_created == 0 and r2.links_created == 0
        assert await _count(db, Decision) == 1 and await _count(db, EngineSignalDecisionLink) == 1
        assert await _count(db, PromotionRun) == 2   # ledger append-only
    _run(go())


# ── 7. promotion_run append-only ─────────────────────────────────────────────

def test_ledger_append_only():
    cols = {c.name for c in sa_inspect(PromotionRun).columns}
    assert "updated_at" not in cols


# ── 8. no score / forecast / priority fields ─────────────────────────────────

def test_no_score_forecast_priority():
    cols = {c.name for c in sa_inspect(PromotionRun).columns}
    rnames = {f.name for f in dc_fields(PromotionRunResult)}
    for bad in ("score", "forecast", "priority", "rank", "weight", "roi", "pnl"):
        assert bad not in cols and bad not in rnames, bad


# ── 9. Feed builder surfaces source_context.decision_id ──────────────────────

def test_feed_builder_surfaces_decision_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # a live signal that already carries a decision_id → builder exposes it
        db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
               insight_key="adv_ad_on_low_stock:wb:SKU1", marketplace="wb", sku="SKU1",
               status="active", decision_id="dec-1", what="x", why="y", what_to_do="w",
               expected_effect="z", created_at=T0))
        await db.commit()
        feed = await build_feed(db, user_id=uid, now=T0)
        adv = next(i for i in feed if i.contour == "advertising")
        assert adv.source_context.get("decision_id") == "dec-1"
    _run(go())
