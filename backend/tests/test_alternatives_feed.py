"""
Alternatives Feed / UX clarity — group several Decisions of ONE problem under one
group_key (= canonical insight_key) in the Decision Feed, each keeping its own
decision_id / action_key / Apply. action_role = primary (registry's first lever) |
alternative. No schema, no Apply, no Effect/Learning change.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink

from services.decision_feed.builder import build_feed, FeedItem, _action_role


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _link(db, uid, *, insight_key, action_key, mp, sku):
    """A promoted decision link (no observation yet → not_measured_yet, visible)."""
    db.add(EngineSignalDecisionLink(
        user_id=uid, contour="pricing", signal_table="pricing_signal",
        signal_id=str(uuid.uuid4()), insight_key=insight_key, action_key=action_key,
        decision_id=str(uuid.uuid4()), link_status="promoted", marketplace=mp, sku=sku))
    await db.commit()


def _do_items(items):
    return [i for i in items if i.contour == "decision_outcome"]


# ── (1) FeedItem carries group_key / action_key / action_role ────────────────

def test_feeditem_has_alternative_fields():
    names = {f for f in FeedItem.__dataclass_fields__}
    assert {"group_key", "action_key", "action_role"} <= names


# ── (2)(3) two levers of one signal → one group_key, primary + alternative ───

def test_negative_margin_two_items_one_group():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        items = _do_items(await build_feed(db, user_id=uid))
        assert len(items) == 2
        assert {i.group_key for i in items} == {ikey}            # one group
        by_ak = {i.action_key: i for i in items}
        assert by_ak["set_price"].action_role == "primary"
        assert by_ak["reduce_discount"].action_role == "alternative"
        assert by_ak["set_price"].item_key != by_ak["reduce_discount"].item_key   # own decision_id
    _run(go())


# ── (4) WB and Ozon do not share a group (group_key carries marketplace) ─────

def test_marketplace_isolation_distinct_groups():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _link(db, uid, insight_key="pricing_negative_margin:wb:SKU1",
                    action_key="set_price", mp="wb", sku="SKU1")
        await _link(db, uid, insight_key="pricing_negative_margin:ozon:SKU1",
                    action_key="set_price", mp="ozon", sku="SKU1")
        items = _do_items(await build_feed(db, user_id=uid))
        groups = {i.group_key for i in items}
        assert groups == {"pricing_negative_margin:wb:SKU1", "pricing_negative_margin:ozon:SKU1"}
        assert len(groups) == 2   # never merged across marketplaces
    _run(go())


# ── (5) single-action signal → one item, primary ─────────────────────────────

def test_single_action_one_item_primary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_price_below_floor:wb:SKU1"   # only set_price
        await _link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        items = _do_items(await build_feed(db, user_id=uid))
        assert len(items) == 1
        assert items[0].group_key == ikey and items[0].action_role == "primary"
    _run(go())


# ── (6) the five doctrine fields remain present ──────────────────────────────

def test_doctrine_fields_present():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _link(db, uid, insight_key="pricing_negative_margin:wb:SKU1",
                    action_key="set_price", mp="wb", sku="SKU1")
        it = _do_items(await build_feed(db, user_id=uid))[0]
        for f in ("what_happened", "why_it_matters", "meaning",
                  "recommended_action", "expected_effect"):
            assert hasattr(it, f)
        # content fields from the summary are non-empty (never fabricated, but present)
        assert it.what_happened and it.why_it_matters and it.recommended_action
    _run(go())


# ── action_role helper unit (fallbacks) ──────────────────────────────────────

def test_action_role_helper():
    assert _action_role("pricing_negative_margin:wb:SKU1", "set_price") == "primary"
    assert _action_role("pricing_negative_margin:wb:SKU1", "reduce_discount") == "alternative"
    assert _action_role("pricing_price_below_floor:wb:SKU1", "set_price") == "primary"
    assert _action_role("x:wb:SKU1", None) is None             # no action → no role
    assert _action_role("unknown_legacy:wb:SKU1", "set_price") == "primary"   # safe fallback
