"""
Learning Ranking Explanation in Feed — explain WHY a NOT-yet-measured lever is
primary, from observed Learning OS history only. Shown ONLY for a learning-chosen
primary with >= 10 marketplace-isolated observations preferred over an alternative
with history. Silent on fallback / sub-sample / alternative. No schema/Effect/
Learning/Apply/DTO change, no new metric, no forecast.
"""
import asyncio
import re
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

# words/meanings that must NEVER appear (forecast / guarantee / promise)
_BANNED = ("гаранти", "предсказ", "ожидаемо сработ")
# "лучший/лучше" is banned, but the allowed word "улучшение" legitimately contains
# the same letters — match the stem only at a word boundary.
_BANNED_BEST = re.compile(r"\bлучш")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _decision_link(db, uid, *, insight_key, action_key, mp, sku):
    """Promoted, not-yet-measured decision link → a feed group member."""
    db.add(EngineSignalDecisionLink(
        user_id=uid, contour="pricing", signal_table="pricing_signal",
        signal_id=str(uuid.uuid4()), insight_key=insight_key, action_key=action_key,
        decision_id=str(uuid.uuid4()), link_status="promoted", marketplace=mp, sku=sku))
    await db.commit()


async def _history(db, uid, *, action_key, mp, improved, worsened=0, insight_key="hist:past:SKU"):
    """MEASURED observations for (mp, action_key); decision_id None → never a feed item."""
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


async def _margin_group(db, uid, mp="wb"):
    ikey = f"pricing_negative_margin:{mp}:SKU1"
    await _decision_link(db, uid, insight_key=ikey, action_key="set_price", mp=mp, sku="SKU1")
    await _decision_link(db, uid, insight_key=ikey, action_key="reduce_discount", mp=mp, sku="SKU1")
    return ikey


# ── (1) primary with >= 10 history gets ranking_explain ──────────────────────

def test_primary_gets_explanation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _margin_group(db, uid)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        await _history(db, uid, action_key="set_price", mp="wb", improved=2, worsened=3)  # inferior, observed
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        prim = items["reduce_discount"]
        assert prim.action_role == "primary"
        assert prim.ranking_explain is not None
        assert prim.ranking_explain["explanation_text"]
        assert prim.ranking_explain["sample_size"] >= 10
        assert "set_price" in prim.ranking_explain["compared_action_keys"]
    _run(go())


# ── (2) sub-sample (<10) → None ──────────────────────────────────────────────

def test_sub_sample_no_explanation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _margin_group(db, uid)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=9)
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        # set_price stays primary (sub-sample), and carries no explanation
        assert items["set_price"].action_role == "primary"
        assert items["set_price"].ranking_explain is None
        assert items["reduce_discount"].ranking_explain is None
    _run(go())


# ── (3) no history → None ────────────────────────────────────────────────────

def test_no_history_no_explanation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _margin_group(db, uid)
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        assert items["set_price"].action_role == "primary"
        assert items["set_price"].ranking_explain is None
    _run(go())


# ── (4) alternative item → None ──────────────────────────────────────────────

def test_alternative_has_no_explanation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _margin_group(db, uid)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        alt = items["set_price"]
        assert alt.action_role == "alternative"
        assert alt.ranking_explain is None
    _run(go())


# ── (5) WB history does not explain Ozon ─────────────────────────────────────

def test_marketplace_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        wb = await _margin_group(db, uid, mp="wb")
        oz = await _margin_group(db, uid, mp="ozon")
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)   # WB only
        await _history(db, uid, action_key="set_price", mp="wb", improved=2, worsened=3)  # WB inferior, observed
        items = _do_items(await build_feed(db, user_id=uid))
        wb_items = _by_ak([i for i in items if i.group_key == wb])
        oz_items = _by_ak([i for i in items if i.group_key == oz])
        assert wb_items["reduce_discount"].ranking_explain is not None   # WB explained
        # Ozon primary is set_price (fallback) and stays silent
        assert oz_items["set_price"].action_role == "primary"
        assert oz_items["set_price"].ranking_explain is None
        assert oz_items["reduce_discount"].ranking_explain is None
    _run(go())


# ── (6) measured-item path still attaches ranking_explain (no regression) ─────

def test_measured_path_not_broken():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = "pricing_negative_margin:wb:SKU1"
        # a MEASURED reduce_discount decision (own observation) + >= threshold history
        link_id = str(uuid.uuid4())
        db.add(EngineSignalDecisionLink(
            id=link_id, user_id=uid, contour="pricing", signal_table="pricing_signal",
            signal_id=str(uuid.uuid4()), insight_key=ikey, action_key="reduce_discount",
            decision_id=str(uuid.uuid4()), link_status="promoted", marketplace="wb", sku="SKU1"))
        db.add(EngineEffectObservation(
            link_id=link_id, user_id=uid, insight_key=ikey, metric_key="margin",
            measured_at=datetime.utcnow(), effect_band="improved"))
        await db.commit()
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        await _history(db, uid, action_key="set_price", mp="wb", improved=0, worsened=10)
        items = _do_items(await build_feed(db, user_id=uid, include_resolved=True))
        measured = [i for i in items if i.action_key == "reduce_discount" and i.effect_status]
        assert measured and measured[0].ranking_explain is not None   # old v6 path intact
    _run(go())


# ── (7) honesty guard — no banned wording ────────────────────────────────────

def test_honesty_guard():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _margin_group(db, uid)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        await _history(db, uid, action_key="set_price", mp="wb", improved=2, worsened=3)  # inferior, observed
        items = _by_ak(_do_items(await build_feed(db, user_id=uid)))
        txt = items["reduce_discount"].ranking_explain["explanation_text"].lower()
        for bad in _BANNED:
            assert bad not in txt, f"banned wording: {bad}"
        assert not _BANNED_BEST.search(txt), 'banned wording: лучший/лучше'
        # the only allowed "прогноз" is the negated phrase; here we use "не обещание"
        assert "прогноз" not in txt
        assert "наблюдени" in txt   # grounded in observations
    _run(go())


# ── (8) sort-only: count / group_key / action_key unchanged, only explain differs ─

def test_sort_only_only_explanation_changes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ikey = await _margin_group(db, uid)
        before = _do_items(await build_feed(db, user_id=uid))
        assert all(i.ranking_explain is None for i in before)
        await _history(db, uid, action_key="reduce_discount", mp="wb", improved=10)
        await _history(db, uid, action_key="set_price", mp="wb", improved=2, worsened=3)  # inferior, observed
        after = _do_items(await build_feed(db, user_id=uid))
        assert len(before) == len(after) == 2
        assert {i.group_key for i in before} == {i.group_key for i in after} == {ikey}
        assert {i.action_key for i in before} == {i.action_key for i in after} \
            == {"set_price", "reduce_discount"}
        # only difference: the primary now carries an explanation
        assert _by_ak(after)["reduce_discount"].ranking_explain is not None
    _run(go())
