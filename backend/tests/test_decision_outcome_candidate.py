"""
Decision Outcome A4 — promotion candidate engine tests.

Read-only classification of normalized engine signals into promotion candidates.
First failing gate wins: invalid → non_active → no_action → already_linked →
eligible. action_key is None for all types today, so real signals are honestly
blocked_no_action. Review dedup uses the CANONICAL (3-part) key. No DB writes.
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
from services.decision_outcome.snapshot import EngineSignalSnapshot
from services.decision_outcome.candidate_engine import (
    build_promotion_candidates, _classify, EnginePromotionCandidate,
    ELIGIBLE, BLOCKED_NO_ACTION, BLOCKED_INVALID, BLOCKED_NON_ACTIVE, BLOCKED_ALREADY_LINKED,
)

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, status="active", action_key="set_price", canonical="seo_title_too_short:wb:SKU1"):
    return EngineSignalSnapshot(
        contour="seo", signal_table="seo_signal", signal_id="s1",
        raw_insight_key=canonical, canonical_insight_key=canonical,
        marketplace="wb", sku="SKU1", action_key=action_key, metric_key="search_visibility",
        status=status, evidence_hash="h", created_at=T0, source_context={"signal_key": "seo_x"})


# ── unit: gate ordering on _classify ─────────────────────────────────────────

def test_eligible_when_active_with_action_unlinked():
    c = _classify(_snap(), linked=set())
    assert c.promotion_status == ELIGIBLE and c.reason is None


def test_blocked_no_action_when_action_none():
    c = _classify(_snap(action_key=None), linked=set())
    assert c.promotion_status == BLOCKED_NO_ACTION


def test_blocked_non_active_beats_no_action():
    # acknowledged + no action → non_active gate wins (checked first)
    c = _classify(_snap(status="acknowledged", action_key=None), linked=set())
    assert c.promotion_status == BLOCKED_NON_ACTIVE
    for st in ("resolved", "dismissed", "promoted_to_decision"):
        assert _classify(_snap(status=st), linked=set()).promotion_status == BLOCKED_NON_ACTIVE


def test_blocked_already_linked():
    snap = _snap()
    linked = {(snap.canonical_insight_key, snap.action_key)}
    assert _classify(snap, linked).promotion_status == BLOCKED_ALREADY_LINKED


# ── e2e: invalid item → blocked_invalid_signal ───────────────────────────────

def test_invalid_item_blocked():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_bogus",
               problem_type="bogus", insight_key="seo_bogus:wb:SKU1", status="active"))
        await db.commit()
        cands = await build_promotion_candidates(db, user_id=uid)
        assert len(cands) == 1 and cands[0].promotion_status == BLOCKED_INVALID
        assert "unknown_signal_type" in cands[0].reason
    _run(go())


# ── e2e: real signals today (action_key None) → blocked_no_action ────────────

def test_real_active_signal_blocked_no_action():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
               marketplace="wb", sku="SKU1", status="active"))
        await db.commit()
        c = (await build_promotion_candidates(db, user_id=uid))[0]
        assert c.promotion_status == BLOCKED_NO_ACTION   # honest: no action invented
    _run(go())


# ── e2e: non-active real signal → blocked_non_active_status ──────────────────

def test_real_non_active_signal_blocked():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
               status="dismissed"))
        await db.commit()
        c = (await build_promotion_candidates(db, user_id=uid))[0]
        assert c.promotion_status == BLOCKED_NON_ACTIVE
    _run(go())


# ── e2e: Review dedup uses the CANONICAL key (registry action patched) ───────

def test_review_dedup_uses_canonical_key():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(ReviewSignal(audit_id=aid, user_id=uid, review_id="rev-99",
               signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
               insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
               marketplace="wildberries", sku="SKU3", status="active"))
        # link stored against the CANONICAL 3-part key + an action
        canonical = "rev_unanswered_negative_review:wildberries:SKU3"
        db.add(EngineSignalDecisionLink(user_id=uid, contour="review", signal_table="review_signal",
               signal_id="x", insight_key=canonical, action_key="reply_manually"))
        await db.commit()

        # patch registry so the review type carries an action_key (None by default)
        key = "rev_unanswered_negative_review"
        orig = snap_mod.BY_SIGNAL_KEY[key]
        snap_mod.BY_SIGNAL_KEY[key] = dataclasses.replace(orig, action_key="reply_manually")
        try:
            c = (await build_promotion_candidates(db, user_id=uid))[0]
        finally:
            snap_mod.BY_SIGNAL_KEY[key] = orig
        assert c.canonical_insight_key == canonical            # 3-part canonical
        assert c.promotion_status == BLOCKED_ALREADY_LINKED    # matched via canonical, not raw 4-part
    _run(go())


# ── e2e: no DB writes ────────────────────────────────────────────────────────

def test_no_db_writes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1", status="active"))
        await db.commit()
        before = (await db.execute(select(func.count()).select_from(SeoSignal))).scalar()
        await build_promotion_candidates(db, user_id=uid)
        assert (await db.execute(select(func.count()).select_from(EngineSignalDecisionLink))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(EngineEffectObservation))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(SeoSignal))).scalar() == before
    _run(go())
