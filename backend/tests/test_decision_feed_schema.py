"""
Daily Decision Feed A2 — data foundation schema + registry tests.

decision_feed_state holds ONLY per-user attention state (never a signal), keyed by
a canonical item_key, unique per (user_id, item_key). FEED_SOURCES is a pure
contract covering all six contours. No score / forecast / ranking / priority number.
Marketplace-agnostic.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision_feed_state import DecisionFeedState

from services.decision_feed.registry import (
    FEED_SOURCES, BY_CONTOUR, CONTOURS,
    ITEM_KEY_CANONICAL_INSIGHT, ITEM_KEY_DECISION_ID,
)

T0 = datetime(2026, 6, 21)
T1 = datetime(2026, 6, 22)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── 1. state round-trip ──────────────────────────────────────────────────────

def test_state_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(DecisionFeedState(user_id=uid, item_key="legal_content_claim_risk:wildberries:SKU1",
                                 contour="legal", state="new", created_at=T0))
        await db.commit()
        s = (await db.execute(select(DecisionFeedState))).scalar_one()
        assert s.state == "new" and s.contour == "legal" and s.snooze_until is None
        assert s.item_key == "legal_content_claim_risk:wildberries:SKU1"
    _run(go())


# ── 2. unique(user_id, item_key) ─────────────────────────────────────────────

def test_unique_user_item():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(DecisionFeedState(user_id=uid, item_key="seo_title_too_short:wb:SKU1",
                                 contour="seo", created_at=T0))
        await db.commit()
        db.add(DecisionFeedState(user_id=uid, item_key="seo_title_too_short:wb:SKU1",
                                 contour="seo", created_at=T0))
        raised = False
        try:
            await db.commit()
        except Exception:
            raised = True; await db.rollback()
        assert raised
    _run(go())


# ── 3. lifecycle state mutates; updated_at changes ───────────────────────────

def test_state_mutates_updated_at():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        s = DecisionFeedState(user_id=uid, item_key="growth_margin_expansion_candidate:ozon:SKU1",
                              contour="growth", state="new", created_at=T0)
        db.add(s); await db.commit()
        assert s.updated_at is None
        s.state = "snoozed"; s.snooze_until = T1; s.updated_at = T1
        await db.commit()
        got = (await db.execute(select(DecisionFeedState))).scalar_one()
        assert got.state == "snoozed" and got.updated_at == T1 and got.snooze_until == T1
        # full lifecycle vocabulary storable
        for st in ("seen", "acted", "dismissed"):
            got.state = st; await db.commit()
            assert (await db.execute(select(DecisionFeedState))).scalar_one().state == st
    _run(go())


# ── 4. item_key uses canonical policy (no raw / 4-part Review key) ───────────

def test_item_key_canonical_policy():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # Review feed item uses the canonical 3-part key — review_id NOT in the key
        review_key = "rev_unanswered_negative_review:wildberries:SKU3"
        db.add(DecisionFeedState(user_id=uid, item_key=review_key, contour="review", created_at=T0))
        # Decision Outcome item uses decision_id
        db.add(DecisionFeedState(user_id=uid, item_key=str(uuid.uuid4()),
                                 contour="decision_outcome", created_at=T0))
        await db.commit()
        rev = (await db.execute(select(DecisionFeedState).where(
            DecisionFeedState.contour == "review"))).scalar_one()
        assert rev.item_key.count(":") == 2 and ":rev-" not in rev.item_key   # 3-part, no review_id
        # registry declares the rule, not a new format
        assert BY_CONTOUR["review"].item_key_rule == ITEM_KEY_CANONICAL_INSIGHT
        assert BY_CONTOUR["decision_outcome"].item_key_rule == ITEM_KEY_DECISION_ID
    _run(go())


# ── 5. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for i, mp in enumerate(("wildberries", "ozon", "yandex")):
            db.add(DecisionFeedState(user_id=uid, item_key=f"adv_ad_destroying_profit:{mp}:SKU1",
                                     contour="advertising", created_at=T0))
        await db.commit()
        keys = {s.item_key for s in (await db.execute(select(DecisionFeedState))).scalars().all()}
        assert any(":wildberries:" in k for k in keys) and any(":yandex:" in k for k in keys)
    _run(go())


# ── 6. registry covers all six contours ──────────────────────────────────────

def test_registry_covers_six_contours():
    assert set(CONTOURS) == {"seo", "advertising", "review", "growth", "legal", "decision_outcome"}
    assert len(FEED_SOURCES) == 6
    for s in FEED_SOURCES:
        assert s.signal_table and s.item_key_rule and s.lifecycle_source and s.priority_source
    # canonical policy reused: 5 engines → canonical insight_key, DO → decision_id
    assert BY_CONTOUR["decision_outcome"].item_key_rule == ITEM_KEY_DECISION_ID
    for c in ("seo", "advertising", "review", "growth", "legal"):
        assert BY_CONTOUR[c].item_key_rule == ITEM_KEY_CANONICAL_INSIGHT


# ── 7. no score / forecast / ranking / priority-number ───────────────────────

def test_no_score_forecast_ranking_fields():
    cols = {c.name for c in sa_inspect(DecisionFeedState).columns}
    for bad in ("score", "forecast", "rank", "ranking", "priority", "priority_score",
                "weight", "health_score", "growth_score"):
        assert bad not in cols, f"decision_feed_state.{bad}"
    # registry declares a priority_source (existing field name) but computes nothing
    from dataclasses import fields as dc_fields
    fnames = {f.name for f in dc_fields(FEED_SOURCES[0].__class__)}
    for bad in ("priority_value", "rank", "score", "weight"):
        assert bad not in fnames


# ── 8. append-only neighbours untouched: state row carries updated_at (lifecycle)

def test_is_lifecycle_entity():
    cols = {c.name for c in sa_inspect(DecisionFeedState).columns}
    assert "updated_at" in cols and "created_at" in cols   # lifecycle, not append-only
