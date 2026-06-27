"""
Decision Priority Engine v1 — within ONE lifecycle/effect bucket, the observed
severity class (priority_level) ranks more serious problems higher; recency is the
final tiebreak. Sort-only: no item dropped, no DTO field, no group change, no schema/
Learning/Effect/Apply change. Uses ONLY priority_level + _order_bucket + created_at —
never pnl_impact / *_score / estimated_value / forecast.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_signal import SeoSignal
from models.engine_signal_decision_link import EngineSignalDecisionLink

from services.decision_feed.builder import build_feed
from routers.decision_feed import FeedItemView

_T0 = datetime(2026, 1, 1, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seo(db, uid, *, sku, priority, created, status="active",
               key="seo_title_too_short"):
    """One active SEO signal → an engine FeedItem in the `active` bucket."""
    db.add(SeoSignal(
        user_id=uid, audit_id=str(uuid.uuid4()), signal_key=key,
        problem_type=key.replace("seo_", ""), insight_key=f"{key}:wb:{sku}",
        marketplace="wb", sku=sku, priority_level=priority, status=status,
        what="что", why="почему", what_to_do="сделать", created_at=created))
    await db.commit()


def _skus(items):
    return [i.sku for i in items]


# ── (1) within one bucket: critical > high > medium > low ─────────────────────

def test_priority_order_within_bucket():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # seed in scrambled order; same status (active) + same created_at
        await _seo(db, uid, sku="low",      priority="low",      created=_T0)
        await _seo(db, uid, sku="critical", priority="critical", created=_T0)
        await _seo(db, uid, sku="medium",   priority="medium",   created=_T0)
        await _seo(db, uid, sku="high",     priority="high",     created=_T0)
        items = await build_feed(db, user_id=uid)
        assert _skus(items) == ["critical", "high", "medium", "low"]
    _run(go())


# ── (2) equal priority → fresher first ───────────────────────────────────────

def test_recency_tiebreak_within_priority():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seo(db, uid, sku="older", priority="high", created=_T0)
        await _seo(db, uid, sku="newer", priority="high", created=_T0 + timedelta(hours=5))
        items = await build_feed(db, user_id=uid)
        assert _skus(items) == ["newer", "older"]
    _run(go())


# ── (3) unknown/None priority sinks to the end of its bucket ─────────────────

def test_none_priority_sinks_within_bucket():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seo(db, uid, sku="none",   priority=None,    created=_T0 + timedelta(hours=9))  # freshest
        await _seo(db, uid, sku="bogus",  priority="weird", created=_T0 + timedelta(hours=8))  # unknown class
        await _seo(db, uid, sku="low",    priority="low",   created=_T0)                        # oldest, known
        items = await build_feed(db, user_id=uid)
        # known 'low' outranks unknown/None despite being older; None/unknown share bucket 4
        assert _skus(items)[0] == "low"
        assert set(_skus(items)[1:]) == {"none", "bogus"}
    _run(go())


# ── (4) _ORDER dominates priority — bucket wins even vs critical ──────────────

def test_order_bucket_dominates_priority():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # acknowledged bucket (_ORDER 4) with CRITICAL priority
        await _seo(db, uid, sku="ack_critical", priority="critical", created=_T0,
                   status="acknowledged")
        # active bucket (_ORDER 2) with LOW priority
        await _seo(db, uid, sku="active_low", priority="low", created=_T0)
        items = await build_feed(db, user_id=uid)
        # active (earlier bucket) outranks acknowledged even though the latter is critical
        assert _skus(items) == ["active_low", "ack_critical"]
    _run(go())


# ── (5) sort-only: item count unchanged ──────────────────────────────────────

def test_sort_only_count_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for i, p in enumerate(("critical", "high", "medium", "low", None)):
            await _seo(db, uid, sku=f"s{i}", priority=p, created=_T0 + timedelta(hours=i))
        items = await build_feed(db, user_id=uid)
        assert len(items) == 5
    _run(go())


# ── (6) DTO exposes no priority field ────────────────────────────────────────

def test_dto_has_no_priority_field():
    names = set(FeedItemView.model_fields)
    for forbidden in ("priority", "priority_level", "_priority_bucket", "_order_bucket"):
        assert forbidden not in names, f"DTO leaked internal field: {forbidden}"


# ── (7)(8) alternatives group survives the new sort key ──────────────────────

async def _link(db, uid, *, insight_key, action_key, mp, sku):
    db.add(EngineSignalDecisionLink(
        user_id=uid, contour="pricing", signal_table="pricing_signal",
        signal_id=str(uuid.uuid4()), insight_key=insight_key, action_key=action_key,
        decision_id=str(uuid.uuid4()), link_status="promoted", marketplace=mp, sku=sku))
    await db.commit()


def test_alternatives_group_intact_after_sort():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        items = [i for i in await build_feed(db, user_id=uid) if i.contour == "decision_outcome"]
        assert len(items) == 2
        assert {i.group_key for i in items} == {ikey}                       # group intact
        assert {i.action_key for i in items} == {"set_price", "reduce_discount"}
        assert {i.action_role for i in items} == {"primary", "alternative"}  # roles untouched
    _run(go())


# ── (9) marketplace not merged by the priority key ───────────────────────────

def test_marketplace_not_merged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # WB critical + Ozon critical — same priority, distinct marketplaces; both present,
        # never collapsed into one item.
        await _seo(db, uid, sku="WB", priority="critical", created=_T0)
        db.add(SeoSignal(
            user_id=uid, audit_id=str(uuid.uuid4()), signal_key="seo_title_too_short",
            problem_type="title_too_short", insight_key="seo_title_too_short:ozon:OZ",
            marketplace="ozon", sku="OZ", priority_level="critical", status="active",
            what="ч", why="п", what_to_do="с", created_at=_T0))
        await db.commit()
        items = await build_feed(db, user_id=uid)
        mps = {(i.marketplace, i.sku) for i in items}
        assert mps == {("wb", "WB"), ("ozon", "OZ")}
        assert len(items) == 2
    _run(go())


# ── (9b) doctrine guard: builder never reads forbidden forecast/score fields ──

def test_builder_uses_no_forbidden_fields():
    import services.decision_feed.builder as b
    src = __import__("inspect").getsource(b)
    for forbidden in ("pnl_impact", "impact_score", "estimated_value", "_score"):
        assert forbidden not in src, f"builder references forbidden field: {forbidden}"
