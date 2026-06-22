"""
Daily Decision Feed A3 — builder tests.

Read-only aggregation of the six contours + decision_feed_state overlay. Live
signals only by default; canonical item_keys; honest attention state; explainable
ordering with no numeric priority. No DB writes, no signal duplication, no
score/forecast/ranking fields.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from dataclasses import fields as dc_fields

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
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_feed.builder import build_feed, FeedItem

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_engines(db, uid, *, seo_status="active"):
    aid = str(uuid.uuid4())
    db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
           marketplace="wb", sku="SKU1", status=seo_status, what="SEO короткий тайтл",
           why="ранжирование", meaning="...", what_to_do="дополнить", expected_effect="охват",
           created_at=T0))
    db.add(AdvertisingSignal(audit_id=aid, user_id=uid, signal_key="adv_ad_destroying_profit",
           problem_type="ad_destroying_profit", insight_key="adv_ad_destroying_profit:wb:SKU1",
           marketplace="wb", sku="SKU1", status="reopened", what="реклама ест прибыль",
           why="DRR", meaning="...", what_to_do="стоп", expected_effect="маржа", created_at=T0))
    db.add(ReviewSignal(audit_id=aid, user_id=uid, review_id="rev-99",
           signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
           insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
           marketplace="wildberries", sku="SKU3", status="acknowledged", what="негатив без ответа",
           why="видно", meaning="...", what_to_do="ответить", expected_effect="риск", created_at=T0))
    db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:ozon:SKU1", marketplace="ozon", sku="SKU1",
           status="active", what="можно поднять цену", why="маржа", meaning="...",
           what_to_do="проверить", expected_effect="маржа", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:wildberries:SKU1",
           marketplace="wildberries", sku="SKU1", status="active", what="формулировки", why="претензии",
           meaning="...", what_to_do="проверить", expected_effect="риск", lifecycle_reason=None,
           created_at=T0))
    await db.commit()


async def _seed_do(db, uid, *, band="improved", sku="SKU9"):
    did = str(uuid.uuid4())
    link = EngineSignalDecisionLink(user_id=uid, contour="advertising", signal_table="advertising_signal",
           signal_id=str(uuid.uuid4()), insight_key=f"adv_ad_destroying_profit:wildberries:{sku}",
           action_key="stop_auto_promotion", decision_id=did, link_status="measured",
           marketplace="wildberries", sku=sku)
    db.add(link); await db.flush()
    db.add(EngineEffectObservation(link_id=link.id, user_id=uid, insight_key=link.insight_key,
           metric_key="ad_profit_impact", window_days=14, baseline_captured_at=T0, measured_at=T0,
           effect_band=band, evidence=json.dumps({"baseline": -500.0, "after": 1000.0})))
    await db.commit()
    return did


# ── 1. collects items from all 6 sources ─────────────────────────────────────

def test_collects_all_six_sources():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        await _seed_do(db, uid, band="improved")
        feed = await build_feed(db, user_id=uid, now=T0)
        contours = {i.contour for i in feed}
        assert contours == {"seo", "advertising", "review", "growth", "legal", "decision_outcome"}
        assert all(isinstance(i, FeedItem) for i in feed)
    _run(go())


# ── 2. missing feed_state → attention_state new ──────────────────────────────

def test_missing_state_is_new():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        feed = await build_feed(db, user_id=uid, now=T0)
        assert feed and all(i.attention_state == "new" for i in feed)
    _run(go())


# ── 3. existing state applied ────────────────────────────────────────────────

def test_existing_state_applied():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        db.add(DecisionFeedState(user_id=uid, item_key="seo_title_too_short:wb:SKU1",
                                 contour="seo", state="seen", created_at=T0))
        await db.commit()
        seo = next(i for i in await build_feed(db, user_id=uid, now=T0) if i.contour == "seo")
        assert seo.attention_state == "seen"
    _run(go())


# ── 4. snoozed hidden by default; include_snoozed shows ──────────────────────

def test_snoozed_hidden_by_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        db.add(DecisionFeedState(user_id=uid, item_key="seo_title_too_short:wb:SKU1", contour="seo",
                                 state="snoozed", snooze_until=T0 + timedelta(days=3), created_at=T0))
        await db.commit()
        keys = {i.item_key for i in await build_feed(db, user_id=uid, now=T0)}
        assert "seo_title_too_short:wb:SKU1" not in keys
        keys2 = {i.item_key for i in await build_feed(db, user_id=uid, include_snoozed=True, now=T0)}
        assert "seo_title_too_short:wb:SKU1" in keys2
    _run(go())


# ── 5. dismissed hidden by default; include_dismissed shows ──────────────────

def test_dismissed_hidden_by_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        db.add(DecisionFeedState(user_id=uid, item_key="legal_content_claim_risk:wildberries:SKU1",
                                 contour="legal", state="dismissed", created_at=T0))
        await db.commit()
        assert not any(i.contour == "legal" for i in await build_feed(db, user_id=uid, now=T0))
        assert any(i.contour == "legal"
                   for i in await build_feed(db, user_id=uid, include_dismissed=True, now=T0))
    _run(go())


# ── 6. resolved source signal hidden by default; include_resolved shows ──────

def test_resolved_source_hidden_by_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid, seo_status="resolved")
        assert not any(i.contour == "seo" for i in await build_feed(db, user_id=uid, now=T0))
        assert any(i.contour == "seo"
                   for i in await build_feed(db, user_id=uid, include_resolved=True, now=T0))
    _run(go())


# ── 7. Review item_key canonical 3-part (no review_id) ───────────────────────

def test_review_item_key_canonical():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        rev = next(i for i in await build_feed(db, user_id=uid, now=T0) if i.contour == "review")
        assert rev.item_key == "rev_unanswered_negative_review:wildberries:SKU3"
        assert rev.item_key.count(":") == 2 and "rev-99" not in rev.item_key
        assert rev.source_context.get("review_id") == "rev-99"   # preserved in context, not key
    _run(go())


# ── 8. Decision Outcome item_key = decision_id ───────────────────────────────

def test_do_item_key_is_decision_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_do(db, uid, band="worsened")
        do = next(i for i in await build_feed(db, user_id=uid, now=T0) if i.contour == "decision_outcome")
        assert do.item_key == did and do.effect_status == "proven_worsened"
    _run(go())


# ── 9. ordering explainable (no numeric priority) ────────────────────────────

def test_ordering():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)        # adv=reopened, seo/growth/legal=active, review=acknowledged
        await _seed_do(db, uid, band="worsened")
        feed = await build_feed(db, user_id=uid, now=T0)
        order = [i._order_bucket for i in feed]
        # reopened first, acknowledged after the actives, proven_worsened after engines
        assert order[0] == "reopened"
        assert order.index("acknowledged") > order.index("active")
        assert order.index("proven_worsened") > order.index("acknowledged")
    _run(go())


# ── 10. no DB writes ─────────────────────────────────────────────────────────

def test_no_db_writes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        before_states = (await db.execute(select(func.count()).select_from(DecisionFeedState))).scalar()
        before_seo = (await db.execute(select(func.count()).select_from(SeoSignal))).scalar()
        await build_feed(db, user_id=uid, now=T0)
        assert (await db.execute(select(func.count()).select_from(DecisionFeedState))).scalar() == before_states
        assert (await db.execute(select(func.count()).select_from(SeoSignal))).scalar() == before_seo
    _run(go())


# ── 11. no score/forecast/ranking/priority fields on FeedItem ────────────────

def test_no_score_forecast_ranking_fields():
    names = {f.name for f in dc_fields(FeedItem)}
    for bad in ("score", "forecast", "priority", "ranking", "rank", "weight",
                "health_score", "growth_score", "priority_score", "roi"):
        assert bad not in names, bad


# ── 12. no duplicated signals (one item per source row) ──────────────────────

def test_no_duplicate_items():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_engines(db, uid)
        feed = await build_feed(db, user_id=uid, now=T0)
        keys = [i.item_key for i in feed]
        assert len(keys) == len(set(keys))
    _run(go())


# ── 13. promoted_to_decision stays visible with decision_id (A3) ─────────────

def test_promoted_to_decision_visible_with_decision_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
               insight_key="adv_ad_destroying_profit:wb:SKU1", marketplace="wb", sku="SKU1",
               status="promoted_to_decision", decision_id="dec-1", what="реклама ест прибыль",
               why="DRR", meaning="x", what_to_do="стоп", expected_effect="маржа", created_at=T0))
        await db.commit()
        feed = await build_feed(db, user_id=uid, now=T0)
        adv = next(i for i in feed if i.contour == "advertising")
        assert adv.source_status == "promoted_to_decision"
        assert adv.source_context.get("decision_id") == "dec-1"   # apply button prerequisite
        assert adv.contour != "decision_outcome"
    _run(go())


# ── 14. resolved/dismissed still hidden by default ───────────────────────────

def test_resolved_dismissed_still_hidden():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for st in ("resolved", "dismissed"):
            db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
                   signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
                   insight_key=f"adv_ad_destroying_profit:wb:{st}", marketplace="wb", sku=st,
                   status=st, what="x", created_at=T0))
        await db.commit()
        assert not any(i.contour == "advertising" for i in await build_feed(db, user_id=uid, now=T0))
    _run(go())
