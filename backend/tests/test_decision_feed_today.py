"""
Today (One Morning Truth, A17) — read-service tests.

Today is a thin, read-only projection over build_feed. These prove: ordering parity
with build_feed, top_action == first build_today item, empty feed → empty today, no DB
writes, and verbatim doctrine-field passthrough. NOTHING is repointed; only the new
service is exercised.
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
from models.decision_feed_state import DecisionFeedState

from services.decision_feed.builder import build_feed
from services.decision_feed.today import build_today, top_action, TodayAction

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_engines(db, uid):
    aid = str(uuid.uuid4())
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
           marketplace="wb", sku="SKU1", status="active", what="SEO короткий тайтл",
           why="ранжирование", meaning="смысл", what_to_do="дополнить", expected_effect="охват",
           created_at=T0))
    db.add(AdvertisingSignal(audit_id=aid, user_id=uid, signal_key="adv_ad_destroying_profit",
           problem_type="ad_destroying_profit", insight_key="adv_ad_destroying_profit:wb:SKU1",
           marketplace="wb", sku="SKU1", status="reopened", what="реклама ест прибыль",
           why="DRR", meaning="смысл", what_to_do="стоп", expected_effect="маржа", created_at=T0))
    db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:ozon:SKU1", marketplace="ozon", sku="SKU1",
           status="active", what="можно поднять цену", why="маржа", meaning="смысл",
           what_to_do="проверить", expected_effect="маржа", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:wildberries:SKU1",
           marketplace="wildberries", sku="SKU1", status="active", what="формулировки", why="претензии",
           meaning="смысл", what_to_do="проверить", expected_effect="риск", created_at=T0))
    await db.commit()


# ── (1) ordering parity — Today == build_feed (same items, same order) ───────

def test_ordering_parity_with_feed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        feed = await build_feed(db, user_id=uid)
        today = await build_today(db, user_id=uid)
        assert [t.item_key for t in today] == [f.item_key for f in feed]
        assert len(today) == len(feed) >= 1
        assert all(isinstance(t, TodayAction) for t in today)
    _run(go())


# ── (2) top_action == first build_today item ─────────────────────────────────

def test_top_action_is_first_today_item():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        today = await build_today(db, user_id=uid)
        top = await top_action(db, user_id=uid)
        assert top is not None
        assert top == today[0]
        # and it is also the first feed item
        feed = await build_feed(db, user_id=uid)
        assert top.item_key == feed[0].item_key
    _run(go())


# ── (3) empty feed → empty today, top_action None ────────────────────────────

def test_empty_feed_empty_today():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())   # no signals seeded
        assert await build_today(db, user_id=uid) == []
        assert await top_action(db, user_id=uid) is None
    _run(go())


# ── (4) no DB writes — Today is read-only ────────────────────────────────────

def test_no_db_writes():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)

        async def _counts():
            seo = (await db.execute(select(func.count()).select_from(SeoSignal))).scalar()
            adv = (await db.execute(select(func.count()).select_from(AdvertisingSignal))).scalar()
            gro = (await db.execute(select(func.count()).select_from(GrowthSignal))).scalar()
            leg = (await db.execute(select(func.count()).select_from(LegalSignal))).scalar()
            st  = (await db.execute(select(func.count()).select_from(DecisionFeedState))).scalar()
            return (seo, adv, gro, leg, st)

        before = await _counts()
        await build_today(db, user_id=uid)
        await top_action(db, user_id=uid)
        assert await _counts() == before
        # feed-state table stays empty — Today creates no attention rows
        assert before[-1] == 0
    _run(go())


# ── (5) doctrine fields pass through verbatim ────────────────────────────────

def test_doctrine_fields_verbatim():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        feed = await build_feed(db, user_id=uid)
        today = await build_today(db, user_id=uid)
        by_key_feed = {f.item_key: f for f in feed}
        for t in today:
            f = by_key_feed[t.item_key]
            assert t.what_happened == f.what_happened
            assert t.why_it_matters == f.why_it_matters
            assert t.meaning == f.meaning
            assert t.recommended_action == f.recommended_action
            assert t.expected_effect == f.expected_effect
            assert t.contour == f.contour
            assert t.marketplace == f.marketplace
            assert t.sku == f.sku
    _run(go())
