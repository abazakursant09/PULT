"""
Decision Outcome A5 — promotion link creation tests.

Write-service: eligible candidates → engine_signal_decision_link (link_status=
proposed, decision_id=None). Idempotent (unique user/insight/action + savepoint).
Blocked candidates write nothing. Review links use the canonical 3-part key. No
Decision, no engine-signal mutation, no promoted_to_decision, no effect observation.
"""
import asyncio
import dataclasses
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.review_signal import ReviewSignal
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

import services.decision_outcome.snapshot as snap_mod
from services.decision_outcome.promotion import promote_eligible_candidates, PromotionWriteResult

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _patch_action(signal_key, action_key):
    """Give a signal type an executor action_key (None by default in A2-A4)."""
    orig = snap_mod.BY_SIGNAL_KEY[signal_key]
    snap_mod.BY_SIGNAL_KEY[signal_key] = dataclasses.replace(orig, action_key=action_key)
    return (signal_key, orig)


def _restore(saved):
    snap_mod.BY_SIGNAL_KEY[saved[0]] = saved[1]


async def _seed_seo(db, uid, *, status="active", sku="SKU1"):
    db.add(SeoSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key=f"seo_title_too_short:wb:{sku}",
           marketplace="wb", sku=sku, status=status))
    await db.commit()


async def _links(db, uid):
    return (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == uid))).scalars().all()


# ── 1. eligible candidate creates a link ─────────────────────────────────────

def test_eligible_creates_link():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_seo(db, uid)
        saved = _patch_action("seo_title_too_short", "update_card")
        try:
            res = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        finally:
            _restore(saved)
        assert isinstance(res, PromotionWriteResult)
        assert res.created == 1 and res.blocked == 0
        links = await _links(db, uid)
        assert len(links) == 1
        l = links[0]
        assert l.link_status == "proposed" and l.decision_id is None
        assert l.insight_key == "seo_title_too_short:wb:SKU1" and l.action_key == "update_card"
        assert l.contour == "seo" and l.signal_table == "seo_signal"
    _run(go())


# ── 2. blocked_no_action writes nothing ──────────────────────────────────────

def test_blocked_no_action_no_link():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_seo(db, uid)   # registry action_key None → blocked_no_action
        res = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        assert res.created == 0 and res.blocked == 1
        assert len(await _links(db, uid)) == 0
    _run(go())


# ── 3. blocked_invalid_signal writes nothing ─────────────────────────────────

def test_blocked_invalid_no_link():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="seo_bogus",
               problem_type="bogus", insight_key="seo_bogus:wb:SKU1", status="active"))
        await db.commit()
        res = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        assert res.created == 0 and res.blocked == 1
        assert len(await _links(db, uid)) == 0
    _run(go())


# ── 4. blocked_non_active_status writes nothing ──────────────────────────────

def test_blocked_non_active_no_link():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_seo(db, uid, status="dismissed")
        saved = _patch_action("seo_title_too_short", "update_card")
        try:
            res = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        finally:
            _restore(saved)
        assert res.created == 0 and res.blocked == 1
        assert len(await _links(db, uid)) == 0
    _run(go())


# ── 5/6. idempotent: repeat run creates no duplicate ─────────────────────────

def test_repeat_run_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_seo(db, uid)
        saved = _patch_action("seo_title_too_short", "update_card")
        try:
            r1 = await promote_eligible_candidates(db, user_id=uid); await db.commit()
            r2 = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        finally:
            _restore(saved)
        assert r1.created == 1
        assert r2.created == 0 and r2.skipped == 1   # already_linked
        assert len(await _links(db, uid)) == 1
    _run(go())


# ── 7. Review link uses canonical 3-part key ─────────────────────────────────

def test_review_link_uses_canonical_key():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(ReviewSignal(audit_id=str(uuid.uuid4()), user_id=uid, review_id="rev-99",
               signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
               insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
               marketplace="wildberries", sku="SKU3", status="active"))
        await db.commit()
        saved = _patch_action("rev_unanswered_negative_review", "reply_manually")
        try:
            res = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        finally:
            _restore(saved)
        assert res.created == 1
        l = (await _links(db, uid))[0]
        assert l.insight_key == "rev_unanswered_negative_review:wildberries:SKU3"  # 3-part canonical
        assert ":rev-99" not in l.insight_key
    _run(go())


# ── 8/9/10. decision_id None, signal not promoted, no effect observation ─────

def test_no_decision_no_signal_mutation_no_effect():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_seo(db, uid)
        saved = _patch_action("seo_title_too_short", "update_card")
        try:
            await promote_eligible_candidates(db, user_id=uid); await db.commit()
        finally:
            _restore(saved)
        l = (await _links(db, uid))[0]
        assert l.decision_id is None and l.link_status == "proposed"
        # source signal status untouched (NOT promoted_to_decision)
        sig = (await db.execute(select(SeoSignal))).scalars().one()
        assert sig.status == "active"
        # no effect observation created
        assert (await db.execute(select(func.count()).select_from(EngineEffectObservation))).scalar() == 0
    _run(go())
