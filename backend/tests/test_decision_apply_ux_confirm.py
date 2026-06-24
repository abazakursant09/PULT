"""
Decision Apply UX A3 — confirm + real apply tests.

confirm_and_apply_decision previews (authoritative gate), records intent, and only
on applyable hands off to the EXISTING execution bridge with dry_run=False. The real
apply is monkeypatched (a real apply would hit the marketplace client). After a real
success, measurement opens via the existing DO path (best-effort). No Feed/source
mutation, no measurement before success.
"""
import ast
import asyncio
import inspect
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
from models.advertising_signal import AdvertisingSignal
from models.seo_signal import SeoSignal
from models.product_listing import ProductListing
from models.marketplace_connection import MarketplaceConnection
from models.execution_log import ExecutionLog
from models.engine_effect_observation import EngineEffectObservation
from models.decision_apply_intent import DecisionApplyIntent
from models.decision_feed_state import DecisionFeedState

import services.decision_apply_ux.confirm as confirm_mod
from services.decision_apply_ux.confirm import confirm_and_apply_decision, ApplyConfirmResult
from services.action_binding.execution_bridge import BoundExecutionResult

IKEY = "adv_ad_on_low_stock:wildberries:SKU1"  # stop_auto_promotion path (A2.2-bind: overspend moved to ad_set_state)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_applyable(db, uid):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="adv", action_key="stop_auto_promotion",
                    insight_key=IKEY, status="open"))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
           signal_table="advertising_signal", signal_id="sig1", insight_key=IKEY,
           action_key="stop_auto_promotion", decision_id=did, link_status="promoted",
           marketplace="wildberries", sku="SKU1"))
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
           insight_key=IKEY, marketplace="wildberries", sku="SKU1", status="promoted_to_decision"))
    db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb", external_id="SKU1"))
    db.add(MarketplaceConnection(user_id=uid, marketplace="wildberries", status="connected",
                                 scopes=["promotions"]))
    await db.commit()
    return did


async def _seed_advice_only(db, uid):
    did = str(uuid.uuid4())
    ik = "seo_title_too_short:wb:SKU1"
    db.add(Decision(id=did, user_id=uid, problem="seo", action_key=None, insight_key=ik, status="open"))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="seo", signal_table="seo_signal",
           signal_id="s1", insight_key=ik, action_key=None, decision_id=did,
           link_status="promoted", marketplace="wb", sku="SKU1"))
    db.add(SeoSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="seo_title_too_short",
           problem_type="title_too_short", insight_key=ik, marketplace="wb", sku="SKU1", status="active"))
    await db.commit()
    return did


def _fake_exec(ok=True, status="success", log_id="log1", calls=None):
    async def f(db, *, user_id, decision_id, marketplace, sku, dry_run, idempotency_key, now=None):
        if calls is not None:
            calls.append({"dry_run": dry_run, "idempotency_key": idempotency_key})
        return BoundExecutionResult(ok=ok, decision_id=decision_id, action_key="stop_auto_promotion",
                                    payload={"offer_id": sku}, execution_log_id=(log_id if ok else None),
                                    status=status, reason=(None if ok else status))
    return f


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1. preview not applyable → rejected, no real apply ───────────────────────

def test_not_applyable_no_apply():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_advice_only(db, uid)
        async def boom(*a, **k):
            raise AssertionError("execute_bound_decision must NOT be called")
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = boom
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wb", sku="SKU1", idempotency_key="k1")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert isinstance(r, ApplyConfirmResult) and r.ok is False and r.reason == "not_bindable"
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "rejected"
        assert await _count(db, ExecutionLog) == 0 and await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 2. applyable → confirmed intent + execute(dry_run=False, idempotency) ────

def test_applyable_confirms_and_calls_apply():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        calls = []
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=True, calls=calls)
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wildberries", sku="SKU1",
                                                 idempotency_key="idem-1")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert r.ok is True
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "confirmed"
        assert len(calls) == 1 and calls[0]["dry_run"] is False and calls[0]["idempotency_key"] == "idem-1"
    _run(go())


# ── 3. successful apply → ok + execution_log_id + measurement opened ─────────

def test_success_opens_measurement():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=True, status="success", log_id="log9")
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wildberries", sku="SKU1",
                                                 idempotency_key="idem-2")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert r.ok is True and r.execution_log_id == "log9" and r.measurement_opened is True
        assert await _count(db, EngineEffectObservation) == 1   # opened via existing DO path
    _run(go())


# ── 4. apply rejected after confirm → ok False, intent confirmed, no measure ─

def test_rejected_after_confirm():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=False, status="rejected")
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wildberries", sku="SKU1",
                                                 idempotency_key="idem-3")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert r.ok is False and r.status == "rejected" and r.measurement_opened is False
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "confirmed"   # user confirmed; apply failed downstream
        assert await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 5. measurement open failure does not break a successful apply ────────────

def test_measurement_failure_does_not_break_apply():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        orig_e = confirm_mod.execute_bound_decision
        orig_m = confirm_mod.open_effect_measurement
        confirm_mod.execute_bound_decision = _fake_exec(ok=True)
        async def boom_measure(*a, **k):
            raise RuntimeError("measure failed")
        confirm_mod.open_effect_measurement = boom_measure
        try:
            r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                                 marketplace="wildberries", sku="SKU1",
                                                 idempotency_key="idem-4")
        finally:
            confirm_mod.execute_bound_decision = orig_e
            confirm_mod.open_effect_measurement = orig_m
        assert r.ok is True and r.measurement_opened is False   # apply still succeeds
    _run(go())


# ── 6. repeat confirm same idempotency_key → key forwarded each time ─────────

def test_repeat_idempotency_forwarded():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        calls = []
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=True, calls=calls)
        try:
            await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1", idempotency_key="same")
            await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1", idempotency_key="same")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert all(c["idempotency_key"] == "same" for c in calls) and len(calls) == 2
        # dedup of real execution is the executor's job (existing idempotency) — delegated
    _run(go())


# ── 7. no direct marketplace call (static import check) ──────────────────────

def test_no_direct_marketplace_import():
    src = inspect.getsource(confirm_mod)
    tree = ast.parse(src)
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
    for bad in ("wb_client", "ozon_client", "credential_vault"):
        assert not any(bad in m for m in mods), bad


# ── 8/9. no Feed state mutation, no source signal manual mutation ────────────

def test_no_feed_or_signal_mutation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_applyable(db, uid)
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=True)
        try:
            await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1", idempotency_key="idem-5")
        finally:
            confirm_mod.execute_bound_decision = orig
        assert await _count(db, DecisionFeedState) == 0   # feed state untouched
        sig = (await db.execute(select(AdvertisingSignal))).scalars().one()
        assert sig.status == "promoted_to_decision"        # source signal unchanged by confirm
    _run(go())
