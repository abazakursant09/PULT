"""
Canonical Alternatives Foundation — one Signal → several Candidates → several
Decisions, one per admissible action_key, all under the same insight_key.

No new schema, no new marketplace action, no Effect/Learning change. The two
existing advertising levers (ad_set_state, stop_auto_promotion) are used as the two
alternatives via a registry monkeypatch — proving the fan-out without adding
reduce_discount. Single-action signals behave exactly as before.
"""
import asyncio
import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink

import services.decision_outcome.snapshot as snapshot_mod
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.candidate_engine import (
    build_promotion_candidates, ELIGIBLE,
)
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions, PROMOTED

SIG = "adv_ad_destroying_profit"
ALT_A, ALT_B = "ad_set_state", "stop_auto_promotion"   # two existing levers


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _adv(db, uid, *, mp="wildberries", sku="SKU1", itype="ad_destroying_profit"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key=f"adv_{itype}",
           problem_type=itype, insight_key=f"adv_{itype}:{mp}:{sku}", marketplace=mp, sku=sku,
           status="active", what="x", why="y", expected_effect="z", what_to_do="w",
           priority_level="high"))
    await db.commit()


def _patch_two_alternatives(monkeypatch):
    """Give SIG two admissible action_keys (existing levers) via the registry entry."""
    entry = BY_SIGNAL_KEY[SIG]
    patched = dataclasses.replace(entry, action_key=ALT_A, action_keys=(ALT_A, ALT_B))
    monkeypatch.setitem(snapshot_mod.BY_SIGNAL_KEY, SIG, patched)


# ── (1) single action_key: unchanged behaviour ──────────────────────────────

def test_single_action_one_candidate():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)   # registry: SIG → one action_key (ad_set_state)
        cands = [c for c in await build_promotion_candidates(db, user_id=uid)
                 if c.signal_table == "advertising_signal"]
        assert len(cands) == 1 and cands[0].action_key == "ad_set_state"
    _run(go())


# ── (2) two action_keys → two candidates, same insight_key ───────────────────

def test_two_action_keys_two_candidates(monkeypatch):
    _patch_two_alternatives(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        cands = await build_promotion_candidates(db, user_id=uid)
        ikey = "adv_ad_destroying_profit:wildberries:SKU1"
        mine = [c for c in cands if c.canonical_insight_key == ikey]
        assert {c.action_key for c in mine} == {ALT_A, ALT_B}
        assert all(c.promotion_status == ELIGIBLE for c in mine)
        assert len({c.canonical_insight_key for c in mine}) == 1   # same insight_key
    _run(go())


# ── (3) promotion + bridge → two Decisions, distinct action_key ──────────────

def test_promotion_creates_two_decisions(monkeypatch):
    _patch_two_alternatives(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        links = (await db.execute(select(EngineSignalDecisionLink))).scalars().all()
        assert {l.action_key for l in links} == {ALT_A, ALT_B}
        assert len({l.insight_key for l in links}) == 1
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 2 and all(i.outcome == PROMOTED for i in res.items)
        decs = (await db.execute(select(Decision))).scalars().all()
        assert {d.action_key for d in decs} == {ALT_A, ALT_B}
        assert len({d.insight_key for d in decs}) == 1   # one insight_key, two Decisions
    _run(go())


# ── (4) re-run is idempotent (no duplicates) ─────────────────────────────────

def test_rerun_no_duplicates(monkeypatch):
    _patch_two_alternatives(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        # rerun
        r2 = await promote_eligible_candidates(db, user_id=uid); await db.commit()
        b2 = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert r2.created == 0 and b2.promoted == 0
        assert len((await db.execute(select(EngineSignalDecisionLink))).scalars().all()) == 2
        assert len((await db.execute(select(Decision))).scalars().all()) == 2
    _run(go())


# ── (5) marketplace isolation across alternatives ────────────────────────────

def test_marketplace_isolation(monkeypatch):
    _patch_two_alternatives(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, mp="wildberries", sku="SKU1")
        await _adv(db, uid, mp="ozon", sku="SKU1")
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        decs = (await db.execute(select(Decision))).scalars().all()
        wb = {d.action_key for d in decs if d.insight_key.endswith(":wildberries:SKU1")}
        oz = {d.action_key for d in decs if d.insight_key.endswith(":ozon:SKU1")}
        assert wb == {ALT_A, ALT_B} and oz == {ALT_A, ALT_B}   # each MP its own pair
        assert len(decs) == 4                                   # 2 MP × 2 alternatives, no blend
    _run(go())


# ── (8) registry: action_keys consistent; primary is action_keys[0] ──────────

def test_registry_action_keys_consistent():
    for k, e in BY_SIGNAL_KEY.items():
        if e.action_key is None:
            assert e.action_keys == ()                 # advice-only
        else:
            assert e.action_keys[0] == e.action_key    # primary first
            assert len(e.action_keys) == len(set(e.action_keys))   # no dups
    # pricing_negative_margin is the first multi-lever signal (set_price + reduce_discount)
    assert BY_SIGNAL_KEY["pricing_negative_margin"].action_keys == ("set_price", "reduce_discount")
