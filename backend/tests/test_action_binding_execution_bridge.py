"""
Action Catalog Expansion A5 — execution bridge tests.

The bridge validates a bound advertising Decision and hands off to the EXISTING
apply path (services.decision_apply.apply_decision). It never calls a marketplace
directly, never bypasses guard/capability, defaults to dry_run, builds no new
executor, and creates no measurement/effect here.
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
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.product_listing import ProductListing
from models.advertising_signal import AdvertisingSignal
from models.execution_log import ExecutionLog
from models.engine_effect_observation import EngineEffectObservation

import services.action_binding.execution_bridge as eb
from services.action_binding.execution_bridge import (
    execute_bound_decision, BoundExecutionResult, NOT_EXECUTED,
)

T0 = datetime(2026, 6, 22)
IKEY = "adv_ad_destroying_profit:wildberries:SKU1"
_BRIDGE_REJECTS = {"decision_not_found", "link_not_found", "not_bindable",
                   "safety_not_manual_approval", "action_key_mismatch",
                   "unsupported_capability", "payload_not_derivable"}


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, action_key="stop_auto_promotion", mp="wildberries",
                sku="SKU1", with_listing=True, ikey=IKEY):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="adv", action_key=action_key,
                    insight_key=ikey, status="open"))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
           signal_table="advertising_signal", signal_id="sig1", insight_key=ikey,
           action_key="stop_auto_promotion", decision_id=did, link_status="promoted",
           marketplace=mp, sku=sku))
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
           insight_key=ikey, marketplace=mp, sku=sku, status="promoted_to_decision"))
    if with_listing:
        db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb",
                              external_id=sku))
    await db.commit()
    return did


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1. dry_run for bindable adv decision reaches the apply/executor path ─────

def test_dry_run_reaches_apply_path():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wildberries", sku="SKU1", dry_run=True)
        assert isinstance(res, BoundExecutionResult)
        assert res.payload == {"offer_id": "SKU1"} and res.action_key == "stop_auto_promotion"
        # reached the executor (not a bridge-level reject)
        assert res.status != NOT_EXECUTED and res.reason not in _BRIDGE_REJECTS
        assert res.status in ("dry_run_ok", "rejected", "failed", "success")
    _run(go())


# ── 2. payload_not_derivable → executor not called ───────────────────────────

def test_payload_not_derivable_no_executor():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, with_listing=False)   # no listing → offer_id not derivable
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wildberries", sku="SKU1", dry_run=True)
        assert res.ok is False and res.status == NOT_EXECUTED
        assert res.reason == "payload_not_derivable" and res.execution_log_id is None
        assert await _count(db, ExecutionLog) == 0
    _run(go())


# ── 3. safety_class != manual_approval → rejected ────────────────────────────

def test_safety_not_manual_approval_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        key = "adv_ad_destroying_profit"
        orig = eb.BY_SIGNAL_TYPE[key]
        eb.BY_SIGNAL_TYPE[key] = dataclasses.replace(orig, safety_class="manual_only")
        try:
            res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                               marketplace="wildberries", sku="SKU1", dry_run=True)
        finally:
            eb.BY_SIGNAL_TYPE[key] = orig
        assert res.ok is False and res.reason == "safety_not_manual_approval"
        assert res.status == NOT_EXECUTED and await _count(db, ExecutionLog) == 0
    _run(go())


# ── 4. action_key mismatch → rejected ────────────────────────────────────────

def test_action_key_mismatch_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, action_key="set_price")   # Decision != binding action
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wildberries", sku="SKU1", dry_run=True)
        assert res.ok is False and res.reason == "action_key_mismatch"
        assert res.status == NOT_EXECUTED and await _count(db, ExecutionLog) == 0
    _run(go())


# ── 5. unsupported marketplace capability → rejected honestly ────────────────

def test_unsupported_capability_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, mp="yandex", sku="SKU9",
                          ikey="adv_ad_destroying_profit:yandex:SKU9")
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="yandex", sku="SKU9", dry_run=True)
        assert res.ok is False and res.reason == "unsupported_capability"
        assert res.status == NOT_EXECUTED and await _count(db, ExecutionLog) == 0
    _run(go())


# ── 6. decision/link not found → honest reject ───────────────────────────────

def test_decision_and_link_not_found():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await execute_bound_decision(db, user_id=uid, decision_id="ghost",
                                         marketplace="wildberries", sku="SKU1")
        assert r.ok is False and r.reason == "decision_not_found"
        # decision exists but no link
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="x", action_key="stop_auto_promotion",
                        insight_key=IKEY, status="open"))
        await db.commit()
        r2 = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                          marketplace="wildberries", sku="SKU1")
        assert r2.ok is False and r2.reason == "link_not_found"
    _run(go())


# ── 7. idempotency_key is accepted + no measurement/effect created ───────────

def test_idempotency_and_no_measurement():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wildberries", sku="SKU1",
                                           dry_run=True, idempotency_key="idem-123")
        assert res.status != NOT_EXECUTED   # reached apply with the key, no crash
        # the bridge opens no measurement / effect observation
        assert await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 8. source signal not mutated by the bridge itself ────────────────────────

def test_source_signal_not_touched_by_bridge():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        await execute_bound_decision(db, user_id=uid, decision_id=did,
                                     marketplace="wildberries", sku="SKU1", dry_run=True)
        sig = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig.status == "promoted_to_decision"   # unchanged by the bridge
    _run(go())
