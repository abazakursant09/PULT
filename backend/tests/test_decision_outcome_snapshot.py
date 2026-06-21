"""
Decision Outcome A3 — canonical signal snapshot / normalization tests.

Read-only aggregation of the 5 engine signal tables into uniform
EngineSignalSnapshot items. 3-part keys pass through; Review's 4-part key is
normalized to 3-part with review_id preserved in source_context; unknown/bad keys
become InvalidSignalItem (never crash). action_key/metric_key come from the
registry. No DB writes.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.snapshot import (
    build_signal_snapshot, EngineSignalSnapshot, InvalidSignalItem,
)
from services.decision_outcome.registry import BY_SIGNAL_KEY

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_valid(db, uid):
    aid = str(uuid.uuid4())
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key="seo_title_too_short:wildberries:SKU1",
           marketplace="wildberries", sku="SKU1", status="active", evidence_hash="h1", created_at=T0))
    db.add(AdvertisingSignal(audit_id=aid, user_id=uid, signal_key="adv_ad_destroying_profit",
           problem_type="ad_destroying_profit", insight_key="adv_ad_destroying_profit:ozon:SKU2",
           marketplace="ozon", sku="SKU2", status="active", evidence_hash="h2", created_at=T0))
    db.add(ReviewSignal(audit_id=aid, user_id=uid, review_id="rev-99",
           signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
           insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
           marketplace="wildberries", sku="SKU3", status="active", evidence_hash="h3", created_at=T0))
    db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:yandex:SKU4",
           marketplace="yandex", sku="SKU4", status="active", evidence_hash="h4", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk",
           insight_key="legal_content_claim_risk:wildberries:SKU5",
           marketplace="wildberries", sku="SKU5", status="active", evidence_hash="h5", created_at=T0))
    await db.commit()


# ── 1. snapshot collects all 5 contours ──────────────────────────────────────

def test_snapshot_collects_all_contours():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        items = await build_signal_snapshot(db, user_id=uid)
        valid = [i for i in items if isinstance(i, EngineSignalSnapshot)]
        assert {i.contour for i in valid} == {"seo", "advertising", "review", "growth", "legal"}
        assert len(valid) == 5
    _run(go())


# ── 2. canonical == raw for 3-part contours ──────────────────────────────────

def test_three_part_canonical_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        by = {i.contour: i for i in await build_signal_snapshot(db, user_id=uid)
              if isinstance(i, EngineSignalSnapshot)}
        for c in ("seo", "advertising", "growth", "legal"):
            assert by[c].canonical_insight_key == by[c].raw_insight_key
            assert by[c].canonical_insight_key.count(":") == 2
    _run(go())


# ── 3/4. Review 4-part normalized to 3-part, review_id preserved ─────────────

def test_review_normalized_and_review_id_preserved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        rev = next(i for i in await build_signal_snapshot(db, user_id=uid)
                   if isinstance(i, EngineSignalSnapshot) and i.contour == "review")
        assert rev.raw_insight_key == "rev_unanswered_negative_review:wildberries:SKU3:rev-99"
        assert rev.canonical_insight_key == "rev_unanswered_negative_review:wildberries:SKU3"
        assert rev.canonical_insight_key.count(":") == 2
        assert rev.source_context["review_id"] == "rev-99"
    _run(go())


# ── 5. invalid key does not crash → InvalidSignalItem with reason ────────────

def test_invalid_signal_does_not_crash():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        # unknown signal_key (not in registry)
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_bogus_type",
               problem_type="bogus", insight_key="seo_bogus_type:wb:SKU1", status="active"))
        # known signal_key but wrong arity (2 parts)
        db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
               problem_type="margin_expansion_candidate", insight_key="growth_margin_expansion_candidate:wb",
               status="active"))
        await db.commit()
        items = await build_signal_snapshot(db, user_id=uid)
        invalid = [i for i in items if isinstance(i, InvalidSignalItem)]
        reasons = " ".join(i.reason for i in invalid)
        assert len(invalid) == 2
        assert "unknown_signal_type" in reasons and "unexpected_key_arity" in reasons
        # no EngineSignalSnapshot produced for the bad rows
        assert not any(isinstance(i, EngineSignalSnapshot) for i in items)
    _run(go())


# ── 6. action_key / metric_key sourced from the registry ─────────────────────

def test_action_and_metric_from_registry():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        for i in await build_signal_snapshot(db, user_id=uid):
            if isinstance(i, EngineSignalSnapshot):
                entry = BY_SIGNAL_KEY[i.source_context["signal_key"]]
                assert i.metric_key == entry.default_metric_key
                assert i.action_key == entry.action_key   # None in A2/A3 (no executor binding yet)
    _run(go())


# ── 7. no DB writes (read-only) ──────────────────────────────────────────────

def test_no_db_writes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        before = (await db.execute(select(func.count()).select_from(SeoSignal))).scalar()
        await build_signal_snapshot(db, user_id=uid)
        # nothing written to the outcome tables, nothing changed in engine tables
        assert (await db.execute(select(func.count()).select_from(EngineSignalDecisionLink))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(EngineEffectObservation))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(SeoSignal))).scalar() == before
    _run(go())


# ── 8. contour / status filters ──────────────────────────────────────────────

def test_filters():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_valid(db, uid)
        only_legal = await build_signal_snapshot(db, user_id=uid, contour="legal")
        assert len(only_legal) == 1 and only_legal[0].contour == "legal"
        none_dismissed = await build_signal_snapshot(db, user_id=uid, status="dismissed")
        assert none_dismissed == []
    _run(go())
