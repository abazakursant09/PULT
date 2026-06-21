"""
Daily Decision Feed A4 — API tests.

GET feed envelope + filters; attention mutations (seen/snooze/dismiss/act) touch
ONLY decision_feed_state; 404 for unknown item_key; raw 4-part Review key rejected;
past snooze rejected; no score/forecast/ranking fields. Source signals untouched.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.review_signal import ReviewSignal
from models.legal_signal import LegalSignal

from routers import decision_feed as df
from routers.decision_feed import (
    get_decision_feed, feed_seen, feed_snooze, feed_dismiss, feed_act,
    SnoozeBody, FeedResponse, FeedStateResponse, FeedItemView,
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


class _User:
    def __init__(self, uid):
        self.id = uid


SEO_KEY = "seo_title_too_short:wb:SKU1"
LEGAL_KEY = "legal_content_claim_risk:wildberries:SKU1"


async def _seed(db, uid):
    aid = str(uuid.uuid4())
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key=SEO_KEY, marketplace="wb", sku="SKU1",
           status="active", what="короткий тайтл", why="ранж", meaning="x", what_to_do="дополнить",
           expected_effect="охват", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key=LEGAL_KEY, marketplace="wildberries",
           sku="SKU1", status="active", what="формулировки", why="претензии", meaning="x",
           what_to_do="проверить", expected_effect="риск", created_at=T0))
    db.add(ReviewSignal(audit_id=aid, user_id=uid, review_id="rev-99",
           signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
           insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
           marketplace="wildberries", sku="SKU3", status="active", what="негатив", why="видно",
           meaning="x", what_to_do="ответить", expected_effect="риск", created_at=T0))
    await db.commit()


# ── 1. GET envelope ──────────────────────────────────────────────────────────

def test_get_feed_envelope():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        resp = await get_decision_feed(current_user=_User(uid), db=db)
        assert isinstance(resp, FeedResponse)
        assert set(resp.model_dump().keys()) == {"items", "total"} and resp.total == 3
        assert all(i.attention_state == "new" for i in resp.items)
    _run(go())


# ── 2. contour filter ────────────────────────────────────────────────────────

def test_contour_filter():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        resp = await get_decision_feed(contour="legal", current_user=_User(uid), db=db)
        assert resp.total == 1 and resp.items[0].contour == "legal"
    _run(go())


# ── 3. seen creates/updates state ────────────────────────────────────────────

def test_seen_creates_state():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        r = await feed_seen(SEO_KEY, current_user=_User(uid), db=db)
        assert isinstance(r, FeedStateResponse) and r.state == "seen"
        feed = await get_decision_feed(current_user=_User(uid), db=db)
        seo = next(i for i in feed.items if i.item_key == SEO_KEY)
        assert seo.attention_state == "seen"
    _run(go())


# ── 4. snooze hides by default; include_snoozed shows ────────────────────────

def test_snooze_hides_by_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        await feed_snooze(SEO_KEY, SnoozeBody(until=T0 + timedelta(days=3) if False else
                                              datetime.utcnow() + timedelta(days=3)),
                          current_user=_User(uid), db=db)
        keys = {i.item_key for i in (await get_decision_feed(current_user=_User(uid), db=db)).items}
        assert SEO_KEY not in keys
        keys2 = {i.item_key for i in (await get_decision_feed(
            include_snoozed=True, current_user=_User(uid), db=db)).items}
        assert SEO_KEY in keys2
    _run(go())


# ── 5. dismiss hides by default; include_dismissed shows ─────────────────────

def test_dismiss_hides_by_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        await feed_dismiss(LEGAL_KEY, current_user=_User(uid), db=db)
        assert not any(i.item_key == LEGAL_KEY
                       for i in (await get_decision_feed(current_user=_User(uid), db=db)).items)
        assert any(i.item_key == LEGAL_KEY for i in (await get_decision_feed(
            include_dismissed=True, current_user=_User(uid), db=db)).items)
    _run(go())


# ── 6. act → acted ───────────────────────────────────────────────────────────

def test_act_state():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        r = await feed_act(SEO_KEY, current_user=_User(uid), db=db)
        assert r.state == "acted"
    _run(go())


# ── 7. mutation does NOT touch source signal ─────────────────────────────────

def test_mutation_does_not_touch_source():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        await feed_dismiss(SEO_KEY, current_user=_User(uid), db=db)
        sig = (await db.execute(select(SeoSignal))).scalars().one()
        assert sig.status == "active"   # source signal status unchanged
    _run(go())


# ── 8. raw 4-part Review item_key rejected ───────────────────────────────────

def test_raw_review_key_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        with pytest.raises(HTTPException) as ei:
            await feed_seen("rev_unanswered_negative_review:wildberries:SKU3:rev-99",
                            current_user=_User(uid), db=db)
        assert ei.value.status_code == 400
    _run(go())


# ── 9. unknown item_key → 404 ────────────────────────────────────────────────

def test_unknown_item_404():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        with pytest.raises(HTTPException) as ei:
            await feed_seen("seo_title_too_short:wb:NOPE", current_user=_User(uid), db=db)
        assert ei.value.status_code == 404
    _run(go())


# ── 10. snooze in the past → 422 ─────────────────────────────────────────────

def test_snooze_past_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); await _seed(db, uid)
        with pytest.raises(HTTPException) as ei:
            await feed_snooze(SEO_KEY, SnoozeBody(until=datetime.utcnow() - timedelta(days=1)),
                              current_user=_User(uid), db=db)
        assert ei.value.status_code == 422
    _run(go())


# ── 11. no score/forecast/ranking fields in API view ─────────────────────────

def test_no_score_forecast_ranking_in_view():
    for bad in ("score", "forecast", "priority", "ranking", "rank", "weight", "roi"):
        assert bad not in FeedItemView.model_fields


# ── 12. routes mounted ───────────────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in df.router.routes}
    assert "/decision-feed" in paths
    assert any("/decision-feed/{item_key}/seen" == p for p in paths)
    import main
    app_paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/decision-feed" in app_paths
