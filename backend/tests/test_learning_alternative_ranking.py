"""
Learning-driven Alternative Ranking — observed Learning OS history (>= min_sample=10
measured outcomes, marketplace-isolated) picks the PRIMARY lever inside one insight
group of the Decision Feed. SORT-ONLY: nothing dropped, no new Decision/Apply/Effect/
Learning/schema change. Registry lever order is the deterministic fallback.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_feed.builder import build_feed


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _decision_link(db, uid, *, insight_key, action_key, mp, sku):
    """A promoted, not-yet-measured decision link → shows in the feed group."""
    db.add(EngineSignalDecisionLink(
        user_id=uid, contour="pricing", signal_table="pricing_signal",
        signal_id=str(uuid.uuid4()), insight_key=insight_key, action_key=action_key,
        decision_id=str(uuid.uuid4()), link_status="promoted", marketplace=mp, sku=sku))
    await db.commit()


async def _history(db, uid, *, action_key, mp, improved, worsened=0, insight_key="hist:past:SKU"):
    """Seed `improved`+`worsened` MEASURED observations for (mp, action_key). decision_id
    is None so these never become feed items — they only feed the learning aggregate."""
    link_id = str(uuid.uuid4())
    db.add(EngineSignalDecisionLink(
        id=link_id, user_id=uid, contour="pricing", signal_table="pricing_signal",
        signal_id=str(uuid.uuid4()), insight_key=insight_key, action_key=action_key,
        decision_id=None, link_status="observed", marketplace=mp, sku="SKUh"))
    for band, n in (("improved", improved), ("worsened", worsened)):
        for _ in range(n):
            db.add(EngineEffectObservation(
                link_id=link_id, user_id=uid, insight_key=insight_key, metric_key="margin",
                measured_at=datetime.utcnow(), effect_band=band))
    await db.commit()


def _do_items(items):
    return [i for i in items if i.contour == "decision_outcome"]


def _by_ak(items):
    return {i.action_key: i for i in items}


# ── (1) ≥10 observed history flips primary to the better lever ────────────────

def test_learning_makes_better_lever_primary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)   # >= min_sample
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        assert items["reduce_discount"].action_role == "primary"
        assert items["set_price"].action_role == "alternative"
    _run(go())


# ── (2) sub-sample (<10) keeps the registry order ────────────────────────────

def test_sub_sample_keeps_registry_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=9)    # < min_sample
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        assert items["set_price"].action_role == "primary"
        assert items["reduce_discount"].action_role == "alternative"
    _run(go())


# ── (3) no history → registry default ────────────────────────────────────────

def test_no_history_registry_default():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        assert items["set_price"].action_role == "primary"
        assert items["reduce_discount"].action_role == "alternative"
    _run(go())


# ── (4) marketplace isolation: WB history never touches Ozon ──────────────────

def test_marketplace_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        wb = "pricing_negative_margin:wb:SKU1"
        oz = "pricing_negative_margin:ozon:SKU1"
        for ikey, mp in ((wb, "wb"), (oz, "ozon")):
            await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp=mp, sku="SKU1")
            await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp=mp, sku="SKU1")
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)   # WB only
        items = _do_items(await build_feed(db, user_id=uid))
        wb_items = _by_ak([i for i in items if i.group_key == wb])
        oz_items = _by_ak([i for i in items if i.group_key == oz])
        assert wb_items["reduce_discount"].action_role == "primary"   # WB learned
        assert oz_items["set_price"].action_role == "primary"         # Ozon untouched (fallback)
    _run(go())


# ── (5) single action → primary ──────────────────────────────────────────────

def test_single_action_primary():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_price_below_floor:wb:SKU1"   # only set_price
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        items = _do_items(await build_feed(db, user_id=uid))
        assert len(items) == 1 and items[0].action_role == "primary"
    _run(go())


# ── (6) equal counts → tiebreak by registry order ────────────────────────────

def test_equal_counts_tiebreak_registry():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        await _history(db, uid, action_key="set_price", mp="wb", improved=10)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)   # equal
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        assert items["set_price"].action_role == "primary"            # registry-first wins tie
        assert items["reduce_discount"].action_role == "alternative"
    _run(go())


# ── (7) sort-only: same count / group_key / action_key regardless of role ─────

def test_sort_only_no_structural_change():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp="wb", sku="SKU1")
        before = _do_items(await build_feed(db, user_id=uid))
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        after = _do_items(await build_feed(db, user_id=uid))
        assert len(before) == len(after) == 2                          # nothing dropped/added
        assert {i.group_key for i in before} == {i.group_key for i in after} == {ikey}
        assert {i.action_key for i in before} == {i.action_key for i in after} \
            == {"set_price", "reduce_discount"}
    _run(go())


# ── (8) doctrine fields remain ───────────────────────────────────────────────

def test_doctrine_fields_remain():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp="wb", sku="SKU1")
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        it = _do_items(await build_feed(db, user_id=uid))[0]
        for f in ("what_happened", "why_it_matters", "meaning",
                  "recommended_action", "expected_effect"):
            assert hasattr(it, f)
        assert it.what_happened and it.why_it_matters and it.recommended_action
    _run(go())
