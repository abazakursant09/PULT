"""
Action Catalog Expansion v2 — P0 Advertising Completion.

adv_ad_on_bad_listing is the 6th advertising signal type. It reuses the EXISTING
stop_auto_promotion action: offer_id is derivable from sku → listing.external_id,
no new capability / payload builder / measurement. These tests prove the full
existing pipeline works for it — binding, payload, promotion candidate, decision
creation, apply preview, measurement — with NO new action and NO new path.
"""
import asyncio
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.product_listing import ProductListing
from models.marketplace_connection import MarketplaceConnection
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.marketplace import action_catalog
from services.action_binding.registry import (
    BY_SIGNAL_TYPE, BOUND, MANUAL_APPROVAL,
)
from services.action_binding.payload_builder import build_action_payload
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.candidate_engine import build_promotion_candidates, ELIGIBLE
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions, PROMOTED
from services.decision_apply_ux.preview import build_apply_preview
import services.decision_apply_ux.confirm as confirm_mod
from services.decision_apply_ux.confirm import confirm_and_apply_decision
from services.action_binding.execution_bridge import BoundExecutionResult

SIGNAL_TYPE = "adv_ad_on_bad_listing"
ITYPE = "ad_on_bad_listing"
IKEY = "adv_ad_on_bad_listing:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _adv_signal(db, uid, *, mp="wildberries", sku="SKU1", status="active"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key=SIGNAL_TYPE, problem_type=ITYPE,
           insight_key=f"adv_{ITYPE}:{mp}:{sku}", marketplace=mp, sku=sku, status=status,
           what="x", why="y", expected_effect="z", what_to_do="w", priority_level="high"))
    await db.commit()


async def _seed_marketplace(db, uid):
    db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb",
                          external_id="SKU1"))
    db.add(MarketplaceConnection(user_id=uid, marketplace="wildberries", status="connected",
                                 scopes=["promotions"]))
    await db.commit()


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


def _fake_exec_success():
    async def f(db, *, user_id, decision_id, marketplace, sku, dry_run, idempotency_key, now=None):
        return BoundExecutionResult(ok=True, decision_id=decision_id,
                                    action_key="stop_auto_promotion", payload={"offer_id": sku},
                                    execution_log_id="log-bl", status="success", reason=None)
    return f


# ── 1. binding registry: bad_listing is BOUND to stop_auto_promotion ──────────

def test_binding_registry():
    b = BY_SIGNAL_TYPE[SIGNAL_TYPE]
    assert b.bindable is True
    assert b.action_key == "stop_auto_promotion"
    assert b.binding_status == BOUND
    assert b.safety_class == MANUAL_APPROVAL          # never auto
    assert b.required_capability == "promotions"
    assert b.required_capability == action_catalog.get("stop_auto_promotion").required_scope
    # decision_outcome registry surfaces the same real action key
    assert BY_SIGNAL_KEY[SIGNAL_TYPE].action_key == "stop_auto_promotion"
    assert b.action_key in action_catalog.known_actions()   # never fabricated


# ── 2. payload builder: offer_id derived from sku → listing.external_id ───────

def test_payload_builder_derives_offer_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_marketplace(db, uid)
        res = await build_action_payload(db, user_id=uid, signal_type=SIGNAL_TYPE,
                                         marketplace="wildberries", sku="SKU1")
        assert res.ok is True
        assert res.action_key == "stop_auto_promotion"
        assert res.payload == {"offer_id": "SKU1"}   # only derivable field, nothing generated
    _run(go())


# ── 3. promotion candidate generation: bad_listing is ELIGIBLE ────────────────

def test_promotion_candidate_eligible():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv_signal(db, uid)
        cands = {c.canonical_insight_key: c for c in await build_promotion_candidates(db, user_id=uid)}
        c = cands[IKEY]
        assert c.promotion_status == ELIGIBLE
        assert c.action_key == "stop_auto_promotion"
    _run(go())


# ── 4. decision creation: bridge promotes a bad_listing signal on WB ──────────

def test_decision_created_via_bridge():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv_signal(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 1 and res.items[0].outcome == PROMOTED
        d = (await db.execute(select(Decision))).scalars().one()
        assert d.action_key == "stop_auto_promotion"
        assert d.insight_key == IKEY
    _run(go())


# ── 5. apply preview: bad_listing decision is applyable on WB ─────────────────

def test_apply_preview_applyable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv_signal(db, uid)
        await _seed_marketplace(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        did = (await db.execute(select(Decision))).scalars().one().id
        prev = await build_apply_preview(db, user_id=uid, decision_id=did,
                                         marketplace="wildberries", sku="SKU1")
        assert prev.applyable is True
        assert prev.action_key == "stop_auto_promotion"
        assert prev.payload == {"offer_id": "SKU1"}
        assert prev.capability_ok is True and prev.safety_class == MANUAL_APPROVAL
        # preview is read-only: no execution log, no measurement written
        assert await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 6. measurement path: a successful apply opens an effect observation ───────

def test_measurement_opens_on_apply():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv_signal(db, uid)
        await _seed_marketplace(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        did = (await db.execute(select(Decision))).scalars().one().id
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec_success()
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wildberries", sku="SKU1",
                                                 idempotency_key="bl-idem")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert r.ok is True and r.measurement_opened is True
        assert await _count(db, EngineEffectObservation) == 1   # existing DO path, no new measure
    _run(go())
