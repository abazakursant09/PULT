"""
Decision Outcome A6 — decision bridge + action binding tests.

Proposed engine links → real Decisions, capability-gated against the action
catalog. Only catalog-backed, marketplace-supported actions promote; everything
else is skipped (no fabricated action). Decision uses the canonical 3-part
insight_key; Review preserves review_id. Source signal becomes
promoted_to_decision only after the Decision exists. Idempotent. No execution log,
no effect observation.
"""
import asyncio
import dataclasses
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.decision import Decision
from models.execution_log import ExecutionLog
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

import services.decision_outcome.snapshot as snap_mod
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import (
    bridge_links_to_decisions, capability_supported, BridgeResult,
    PROMOTED, SKIPPED_NO_ACTION, SKIPPED_NO_CAPABILITY,
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


async def _seed_adv(db, uid, *, mp="wildberries", sku="SKU1"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
           insight_key=f"adv_ad_destroying_profit:{mp}:{sku}", marketplace=mp, sku=sku,
           status="active", what="Реклама съедает прибыль", why="DRR высокий",
           expected_effect="вернуть маржу", what_to_do="остановить автопродвижение",
           priority_level="critical"))
    await db.commit()


async def _decisions(db):
    return (await db.execute(select(Decision))).scalars().all()


async def _link(db, uid):
    return (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == uid))).scalars().first()


# ── capability gate unit ─────────────────────────────────────────────────────

def test_capability_gate():
    assert capability_supported("stop_auto_promotion", "wildberries") is True
    assert capability_supported("stop_auto_promotion", "ozon") is True
    assert capability_supported("stop_auto_promotion", "yandex") is False   # gated impossible
    assert capability_supported(None, "wildberries") is False
    assert capability_supported("not_a_real_action", "wildberries") is False


# ── 1/4/6. supported action → Decision with canonical key; signal promoted ──

def test_supported_action_creates_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_adv(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        # before bridge: signal still active
        sig0 = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig0.status == "active" and sig0.decision_id is None
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert isinstance(res, BridgeResult) and res.promoted == 1
        ds = await _decisions(db)
        assert len(ds) == 1
        assert ds[0].insight_key == "adv_ad_destroying_profit:wildberries:SKU1"   # canonical 3-part
        assert ds[0].action_key == "ad_set_state"
        link = await _link(db, uid)
        assert link.decision_id == ds[0].id and link.link_status == "promoted"
        sig = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig.status == "promoted_to_decision" and sig.decision_id == ds[0].id
    _run(go())


# ── 2. link without action_key → skipped, no Decision ────────────────────────

def test_link_without_action_skipped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(EngineSignalDecisionLink(user_id=uid, contour="seo", signal_table="seo_signal",
               signal_id="s1", insight_key="seo_title_too_short:wb:SKU1", action_key=None))
        await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 0 and res.skipped == 1
        assert res.items[0].outcome == SKIPPED_NO_ACTION
        assert len(await _decisions(db)) == 0
    _run(go())


# ── 3. unsupported capability → skipped, link stays proposed ─────────────────

def test_unsupported_capability_skipped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_adv(db, uid, mp="yandex", sku="SKU9")   # stop_auto_promotion gated on Yandex
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 0 and res.skipped == 1
        assert res.items[0].outcome == SKIPPED_NO_CAPABILITY
        assert len(await _decisions(db)) == 0
        link = await _link(db, uid)
        assert link.decision_id is None and link.link_status == "proposed"   # untouched
        sig = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig.status == "active"   # NOT promoted
    _run(go())


# ── 5. Review preserves review_id; Decision uses canonical key ───────────────

def test_review_preserves_review_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(ReviewSignal(audit_id=str(uuid.uuid4()), user_id=uid, review_id="rev-99",
               signal_key="rev_unanswered_negative_review", problem_type="unanswered_negative_review",
               insight_key="rev_unanswered_negative_review:wildberries:SKU3:rev-99",
               marketplace="wildberries", sku="SKU3", status="active",
               what="Негатив без ответа", why="видно покупателям", expected_effect="снизить риск",
               what_to_do="ответить вручную", priority_level="critical"))
        # publish_review_response is a real WB catalog action — patch the registry binding
        key = "rev_unanswered_negative_review"
        orig = snap_mod.BY_SIGNAL_KEY[key]
        snap_mod.BY_SIGNAL_KEY[key] = dataclasses.replace(orig, action_key="publish_review_response")
        try:
            await db.commit()
            await promote_eligible_candidates(db, user_id=uid); await db.commit()
            res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        finally:
            snap_mod.BY_SIGNAL_KEY[key] = orig
        assert res.promoted == 1
        item = res.items[0]
        assert item.source_context.get("review_id") == "rev-99"
        d = (await _decisions(db))[0]
        assert d.insight_key == "rev_unanswered_negative_review:wildberries:SKU3"  # canonical 3-part
        assert ":rev-99" not in d.insight_key
        assert "rev-99" in (d.cause or "")   # review_id carried into Decision, not lost
        sig = (await db.execute(select(ReviewSignal))).scalars().one()
        assert sig.review_id == "rev-99"     # source signal untouched
    _run(go())


# ── 7. idempotent: repeat bridge does not duplicate Decision ─────────────────

def test_idempotent_bridge():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_adv(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        r1 = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        r2 = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert r1.promoted == 1 and r2.promoted == 0   # nothing left proposed
        assert len(await _decisions(db)) == 1
    _run(go())


# ── 8/9. no execution log, no effect observation ────────────────────────────

def test_no_execution_no_effect():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_adv(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert (await db.execute(select(func.count()).select_from(ExecutionLog))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(EngineEffectObservation))).scalar() == 0
    _run(go())


# ── 10. no fake action binding (registry honesty) ────────────────────────────

def test_action_bindings_are_real_and_advertising_only():
    # Action Catalog Expansion A3 + v2 P0: the six advertising "stop auto-promotion"
    # types are bound to a real catalog action; everything else stays advice-only (None).
    bound = {c.signal_key: c.action_key for c in BY_SIGNAL_KEY.values() if c.action_key is not None}
    assert bound == {
        # A2.2-bind: direct overspend → ad_set_state (campaign pause)
        "adv_ad_destroying_profit": "ad_set_state",
        "adv_ad_spend_without_sales": "ad_set_state",
        "adv_ad_on_unprofitable_product": "ad_set_state",
        # indirect stock/listing → stop_auto_promotion (unchanged)
        "adv_ad_on_low_stock": "stop_auto_promotion",
        "adv_ad_on_oos_risk": "stop_auto_promotion",
        "adv_ad_on_bad_listing": "stop_auto_promotion",
        # A3/A4-bind: all three pricing signals → set_price
        "pricing_price_below_floor": "set_price",
        "pricing_negative_margin": "set_price",
        "pricing_margin_below_target": "set_price",
    }
    from services.marketplace import action_catalog
    for ak in bound.values():
        assert ak in action_catalog.known_actions()   # never a fabricated action
