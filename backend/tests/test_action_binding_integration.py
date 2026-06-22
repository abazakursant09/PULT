"""
Action Catalog Expansion A3 — Decision Outcome binding integration tests.

The five advertising "stop auto-promotion" types now carry a real action_key from
ACTION_BINDINGS → candidate_engine marks them eligible (active) and the bridge can
promote them where capability is supported. SEO/Review/Growth/Legal stay
blocked_no_action. Yandex stays capability-blocked. No fabricated action_key.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.seo_signal import SeoSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink

from services.marketplace import action_catalog
from services.decision_outcome.candidate_engine import (
    build_promotion_candidates, ELIGIBLE, BLOCKED_NO_ACTION,
)
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import (
    bridge_links_to_decisions, PROMOTED, SKIPPED_NO_CAPABILITY,
)

NEW_ADV = ("ad_spend_without_sales", "ad_on_unprofitable_product",
           "ad_on_low_stock", "ad_on_oos_risk")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _adv(db, uid, itype, *, mp="wildberries", sku="SKU1", status="active"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key=f"adv_{itype}", problem_type=itype,
           insight_key=f"adv_{itype}:{mp}:{sku}", marketplace=mp, sku=sku, status=status,
           what="x", why="y", expected_effect="z", what_to_do="w", priority_level="high"))
    await db.commit()


# ── 1. registry exposes action_key for the 5 advertising types ───────────────

def test_registry_action_keys():
    from services.decision_outcome.registry import BY_SIGNAL_KEY
    for it in ("ad_destroying_profit",) + NEW_ADV:
        ak = BY_SIGNAL_KEY[f"adv_{it}"].action_key
        assert ak == "stop_auto_promotion" and ak in action_catalog.known_actions()


# ── 2. candidate_engine: the new types are eligible when active ──────────────

def test_new_types_eligible():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for it in NEW_ADV:
            await _adv(db, uid, it)
        cands = {c.canonical_insight_key: c for c in await build_promotion_candidates(db, user_id=uid)}
        for it in NEW_ADV:
            c = cands[f"adv_{it}:wildberries:SKU1"]
            assert c.promotion_status == ELIGIBLE and c.action_key == "stop_auto_promotion"
    _run(go())


# ── 3. old adv_ad_destroying_profit still works ──────────────────────────────

def test_old_type_still_eligible():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, "ad_destroying_profit")
        c = (await build_promotion_candidates(db, user_id=uid))[0]
        assert c.promotion_status == ELIGIBLE and c.action_key == "stop_auto_promotion"
    _run(go())


# ── 4. SEO/Growth/Legal stay blocked_no_action ───────────────────────────────

def test_others_blocked_no_action():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=aid, user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
               marketplace="wb", sku="SKU1", status="active"))
        db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
               problem_type="margin_expansion_candidate",
               insight_key="growth_margin_expansion_candidate:wb:SKU1", marketplace="wb",
               sku="SKU1", status="active"))
        db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
               requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:wb:SKU1",
               marketplace="wb", sku="SKU1", status="active"))
        await db.commit()
        for c in await build_promotion_candidates(db, user_id=uid):
            assert c.promotion_status == BLOCKED_NO_ACTION and c.action_key is None
    _run(go())


# ── 5. bridge promotes a new type on a supported marketplace ─────────────────

def test_bridge_promotes_new_type_wb():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, "ad_spend_without_sales", mp="wildberries")
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 1 and res.items[0].outcome == PROMOTED
        d = (await db.execute(select(Decision))).scalars().one()
        assert d.action_key == "stop_auto_promotion"
        assert d.insight_key == "adv_ad_spend_without_sales:wildberries:SKU1"
    _run(go())


# ── 6. Yandex still capability-blocked ───────────────────────────────────────

def test_new_type_yandex_blocked():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, "ad_on_oos_risk", mp="yandex", sku="SKU9")
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 0 and res.items[0].outcome == SKIPPED_NO_CAPABILITY
        assert (await db.execute(select(Decision))).scalars().first() is None
        link = (await db.execute(select(EngineSignalDecisionLink))).scalars().one()
        assert link.decision_id is None and link.link_status == "proposed"
    _run(go())
