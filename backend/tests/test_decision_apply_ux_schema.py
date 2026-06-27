"""
Decision Apply UX A2 — preview + schema tests.

build_apply_preview is read-only eligibility + dry-run preview (no apply, no
executor write, no marketplace call, no measurement). decision_apply_intent is an
append-only ledger. The gate decisions come from the existing execution bridge.
"""
import asyncio
import dataclasses
import uuid
from datetime import datetime

from sqlalchemy import select, func, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.product_listing import ProductListing
from models.advertising_signal import AdvertisingSignal
from models.execution_log import ExecutionLog
from models.engine_effect_observation import EngineEffectObservation
from models.decision_apply_intent import DecisionApplyIntent
from models.marketplace_connection import MarketplaceConnection

import services.action_binding.execution_bridge as eb
from services.decision_apply_ux.preview import (
    build_apply_preview, record_apply_intent, ApplyPreview,
    PAYLOAD_OK, PAYLOAD_NOT_DERIVABLE,
)

# Stock/listing type (stop_auto_promotion, offer_id-derivable) for the preview path.
# Overspend types now bind ad_set_state and are covered by the A2.2-bind tests.
IKEY = "adv_ad_on_low_stock:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, action_key="stop_auto_promotion", mp="wildberries",
                sku="SKU1", with_listing=True, with_link=True, with_connection=False, ikey=IKEY):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="adv", action_key=action_key,
                    insight_key=ikey, status="open"))
    if with_connection:
        # active marketplace connection with the promotions scope → dry_run can pass
        db.add(MarketplaceConnection(user_id=uid, marketplace="wildberries", status="connected",
                                     scopes=["promotions"]))
    if with_link:
        db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
               signal_table="advertising_signal", signal_id="sig1", insight_key=ikey,
               action_key="stop_auto_promotion", decision_id=did, link_status="promoted",
               marketplace=mp, sku=sku))
        db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
               insight_key=ikey, marketplace=mp, sku=sku, status="promoted_to_decision"))
    if with_listing:
        db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb",
                              external_id=sku))
    await db.commit()
    return did


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1. preview for a bound advertising decision → applyable ──────────────────

def test_preview_bound_applyable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, with_connection=True)
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wildberries", sku="SKU1")
        assert isinstance(p, ApplyPreview)
        assert p.applyable is True and p.action_key == "stop_auto_promotion"
        assert p.payload == {"offer_id": "SKU1"} and p.capability_ok is True
        assert p.payload_status == PAYLOAD_OK and p.dry_run_status is not None
        assert p.safety_class == "manual_approval" and p.reason is None
    _run(go())


# ── 2. payload_not_derivable ─────────────────────────────────────────────────

def test_preview_payload_not_derivable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, with_listing=False)
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wildberries", sku="SKU1")
        assert p.applyable is False and p.reason == PAYLOAD_NOT_DERIVABLE
        assert p.payload_status == PAYLOAD_NOT_DERIVABLE and p.payload is None
    _run(go())


# ── 3. unsupported capability (Yandex) ───────────────────────────────────────

def test_preview_unsupported_capability():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, mp="yandex", sku="SKU9",
                          ikey="adv_ad_on_low_stock:yandex:SKU9")
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="yandex", sku="SKU9")
        assert p.applyable is False and p.reason == "unsupported_capability"
        assert p.capability_ok is False
    _run(go())


# ── 4. advice-only decision → not_bindable ───────────────────────────────────

def test_preview_advice_only_not_bindable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, action_key=None,
                          ikey="seo_title_too_short:wb:SKU1")
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wb", sku="SKU1")
        assert p.applyable is False and p.reason == "not_bindable"
    _run(go())


# ── 5. action_key mismatch ───────────────────────────────────────────────────

def test_preview_action_key_mismatch():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, action_key="set_price")
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wildberries", sku="SKU1")
        assert p.applyable is False and p.reason == "action_key_mismatch"
    _run(go())


# ── 6. safety_not_manual_approval ────────────────────────────────────────────

def test_preview_safety_not_manual_approval():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        key = "adv_ad_on_low_stock"
        orig = eb.binding_for_action(key, "stop_auto_promotion")
        manual_only = dataclasses.replace(orig, safety_class="manual_only")
        eb_orig = eb.binding_for_action
        eb.binding_for_action = lambda st, ak: manual_only if st == key else eb_orig(st, ak)
        try:
            p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                          marketplace="wildberries", sku="SKU1")
        finally:
            eb.binding_for_action = eb_orig
        assert p.applyable is False and p.reason == "safety_not_manual_approval"
    _run(go())


# ── 7. NO real execution (dry_run only) ──────────────────────────────────────

def test_no_real_execution():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        await build_apply_preview(db, user_id=uid, decision_id=did,
                                  marketplace="wildberries", sku="SKU1")
        assert await _count(db, ExecutionLog) == 0   # dry_run writes no log
    _run(go())


# ── 8. NO measurement / effect observation created ───────────────────────────

def test_no_measurement_or_effect():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        before = await _count(db, EngineEffectObservation)
        await build_apply_preview(db, user_id=uid, decision_id=did,
                                  marketplace="wildberries", sku="SKU1")
        assert await _count(db, EngineEffectObservation) == before == 0
    _run(go())


# ── 9. decision_apply_intent append-only + roundtrip + no analytics cols ─────

def test_intent_append_only_roundtrip():
    cols = {c.name for c in sa_inspect(DecisionApplyIntent).columns}
    assert "updated_at" not in cols
    for bad in ("score", "forecast", "priority", "rank", "weight", "pnl"):
        assert bad not in cols, bad

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        row = await record_apply_intent(db, user_id=uid, decision_id=did,
                                        action_key="stop_auto_promotion", intent_status="previewed",
                                        dry_run_status="dry_run_ok", marketplace="wildberries")
        await db.commit()
        got = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert got.intent_status == "previewed" and got.action_key == "stop_auto_promotion"
        assert got.decision_id == did
    _run(go())


# ── 10. decision not found ───────────────────────────────────────────────────

def test_preview_decision_not_found():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        p = await build_apply_preview(db, user_id=uid, decision_id="ghost",
                                      marketplace="wildberries", sku="SKU1")
        assert p.applyable is False and p.reason == "decision_not_found"
    _run(go())
