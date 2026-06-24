"""
Decision Apply UX A4 — API tests.

Thin delegation: GET preview → build_apply_preview, POST confirm →
confirm_and_apply_decision. Owner-scoped (foreign decision → 404). Real apply is
monkeypatched (would hit the marketplace client). No executor/marketplace import in
the router, no score/forecast fields.
"""
import ast
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advertising_signal import AdvertisingSignal
from models.product_listing import ProductListing
from models.marketplace_connection import MarketplaceConnection

import services.decision_apply_ux.confirm as confirm_mod
from services.action_binding.execution_bridge import BoundExecutionResult
from routers import decision_apply as da
from routers.decision_apply import (
    decision_apply_preview, decision_apply_confirm, ConfirmRequest,
    PreviewResponse, ConfirmResponse,
)

IKEY = "adv_ad_on_low_stock:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _seed(db, uid, *, with_listing=True, with_connection=True, mp="wildberries",
                sku="SKU1", ikey=IKEY):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="adv", action_key="stop_auto_promotion",
                    insight_key=ikey, status="open"))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
           signal_table="advertising_signal", signal_id="sig1", insight_key=ikey,
           action_key="stop_auto_promotion", decision_id=did, link_status="promoted",
           marketplace=mp, sku=sku))
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
           insight_key=ikey, marketplace=mp, sku=sku, status="promoted_to_decision"))
    if with_listing:
        db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb", external_id=sku))
    if with_connection:
        db.add(MarketplaceConnection(user_id=uid, marketplace="wildberries", status="connected",
                                     scopes=["promotions"]))
    await db.commit()
    return did


def _fake_exec(ok=True, status="success", log_id="log1"):
    async def f(db, *, user_id, decision_id, marketplace, sku, dry_run, idempotency_key, now=None):
        return BoundExecutionResult(ok=ok, decision_id=decision_id, action_key="stop_auto_promotion",
                                    payload={"offer_id": sku}, execution_log_id=(log_id if ok else None),
                                    status=status, reason=(None if ok else status))
    return f


# ── 1. preview applyable ─────────────────────────────────────────────────────

def test_preview_applyable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        r = await decision_apply_preview(did, marketplace="wildberries", sku="SKU1",
                                         current_user=_User(uid), db=db)
        assert isinstance(r, PreviewResponse) and r.applyable is True
        assert r.action_key == "stop_auto_promotion" and r.payload == {"offer_id": "SKU1"}
        assert r.capability_ok is True and r.safety_class == "manual_approval"
    _run(go())


# ── 2. preview payload_not_derivable ─────────────────────────────────────────

def test_preview_payload_not_derivable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, with_listing=False)
        r = await decision_apply_preview(did, marketplace="wildberries", sku="SKU1",
                                         current_user=_User(uid), db=db)
        assert r.applyable is False and r.reason == "payload_not_derivable"
        assert r.payload_status == "payload_not_derivable"
    _run(go())


# ── 3. preview unsupported capability ────────────────────────────────────────

def test_preview_unsupported_capability():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, mp="yandex", sku="SKU9",
                          ikey="adv_ad_on_low_stock:yandex:SKU9", with_connection=False)
        r = await decision_apply_preview(did, marketplace="yandex", sku="SKU9",
                                         current_user=_User(uid), db=db)
        assert r.applyable is False and r.reason == "unsupported_capability" and r.capability_ok is False
    _run(go())


# ── 4. confirm success ───────────────────────────────────────────────────────

def test_confirm_success():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=True, log_id="logA")
        try:
            r = await decision_apply_confirm(
                did, ConfirmRequest(marketplace="wildberries", sku="SKU1", idempotency_key="k1"),
                current_user=_User(uid), db=db)
        finally:
            confirm_mod.execute_bound_decision = orig
        assert isinstance(r, ConfirmResponse) and r.ok is True
        assert r.execution_log_id == "logA" and r.measurement_opened is True and r.intent_id
    _run(go())


# ── 5. confirm rejected ──────────────────────────────────────────────────────

def test_confirm_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        orig = confirm_mod.execute_bound_decision
        confirm_mod.execute_bound_decision = _fake_exec(ok=False, status="rejected")
        try:
            r = await decision_apply_confirm(
                did, ConfirmRequest(marketplace="wildberries", sku="SKU1", idempotency_key="k2"),
                current_user=_User(uid), db=db)
        finally:
            confirm_mod.execute_bound_decision = orig
        assert r.ok is False and r.status == "rejected" and r.measurement_opened is False
    _run(go())


# ── 6. confirm requires idempotency_key ──────────────────────────────────────

def test_confirm_requires_idempotency():
    # pydantic enforces the field at the model boundary
    with pytest.raises(Exception):
        ConfirmRequest(marketplace="wildberries", sku="SKU1")   # missing idempotency_key

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        r = await decision_apply_confirm(
            did, ConfirmRequest(marketplace="wildberries", sku="SKU1", idempotency_key=""),
            current_user=_User(uid), db=db)
        assert r.ok is False and r.reason == "idempotency_key_required"
    _run(go())


# ── 7. owner scope → foreign decision 404 ────────────────────────────────────

def test_owner_scope_404():
    async def go():
        db = await _engine(); owner = str(uuid.uuid4()); attacker = str(uuid.uuid4())
        did = await _seed(db, owner)
        with pytest.raises(HTTPException) as ei:
            await decision_apply_preview(did, marketplace="wildberries", sku="SKU1",
                                         current_user=_User(attacker), db=db)
        assert ei.value.status_code == 404
        with pytest.raises(HTTPException) as ei2:
            await decision_apply_confirm(
                did, ConfirmRequest(marketplace="wildberries", sku="SKU1", idempotency_key="k"),
                current_user=_User(attacker), db=db)
        assert ei2.value.status_code == 404
    _run(go())


# ── 8. routes mounted ────────────────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in da.router.routes}
    assert {"/decision-apply/preview/{decision_id}", "/decision-apply/confirm/{decision_id}"} <= paths
    import main
    app_paths = set(main.app.openapi()["paths"])  # OpenAPI paths: robust on FastAPI 0.136 (flat) and 0.137+ (nested mounts)
    assert "/api/decision-apply/preview/{decision_id}" in app_paths


# ── 9. router does not import the executor / marketplace client ───────────────

def test_router_no_executor_import():
    tree = ast.parse(inspect.getsource(da))
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
    for bad in ("executor", "wb_client", "ozon_client", "marketplace.action_catalog"):
        assert not any(bad in m for m in mods), bad


# ── 10. no score/forecast/roi/money fields in API ────────────────────────────

def test_no_score_forecast_fields():
    for model in (PreviewResponse, ConfirmResponse):
        for bad in ("score", "forecast", "roi", "money", "pnl", "priority"):
            assert bad not in model.model_fields
