"""
Sprint A2 — reduce_discount margin action.

Action registration (catalog + reversible), capability gate (WB/Ozon api,
Yandex impossible), adapter routing (WB discount / Ozon price), measurement
binding (net_profit under margin_crisis, in the measurable allowlist), and the
declarative margin action space (set_price + reduce_discount).
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from services.marketplace import executor, credential_vault, action_catalog, wb_client, ozon_client
from services.marketplace.errors import ExecutionError
from services.marketplace.action_metric_binding import target_metric, problem_action_space
from services import capability_registry
from services import execution_measurement_bridge as emb


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _conn(db, uid, mp="wb"):
    c = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              status="connected", scopes=["prices"])
    db.add(c)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=c.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok")))
    await db.flush()
    return c


# ── registration ─────────────────────────────────────────────────────────────

def test_reduce_discount_registered_reversible():
    spec = action_catalog.get("reduce_discount")
    assert spec.action_type == "reduce_discount"
    assert spec.required_scope == "prices"
    assert spec.reversible is True and spec.reverter is not None


# ── capability matrix ────────────────────────────────────────────────────────

def test_capability_matrix():
    assert executor.capability_for_action("reduce_discount") == "discounts.write"
    assert capability_registry.verdict("discounts.write", "wb") == "api"
    assert capability_registry.verdict("discounts.write", "ozon") == "api"
    assert capability_registry.verdict("discounts.write", "yandex") == "impossible"


# ── adapter routing (WB discount, Ozon price) ────────────────────────────────

def test_wb_routes_to_set_discount(monkeypatch):
    seen = {}
    async def fake(*, token, offer_id, discount):
        seen.update(offer_id=offer_id, discount=discount)
        return {"requestId": "wb1"}
    monkeypatch.setattr(action_catalog.wb_client, "set_discount", fake)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, "wildberries")   # WB connections store the full label
        res = await executor.execute(db=db, user_id=uid, action_type="reduce_discount",
                                     payload={"marketplace": "wildberries", "offer_id": "123", "discount": 5},
                                     decision_id="d1")
        assert res.status == "success" and seen == {"offer_id": "123", "discount": 5.0}
    _run(go())


def test_ozon_routes_to_set_price(monkeypatch):
    seen = {}
    async def fake(*, token, client_id, offer_id, price):
        seen.update(offer_id=offer_id, price=price)
        return {"requestId": "oz1"}
    monkeypatch.setattr(action_catalog.ozon_client, "set_price", fake)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, "ozon")
        res = await executor.execute(db=db, user_id=uid, action_type="reduce_discount",
                                     payload={"marketplace": "ozon", "offer_id": "OF1", "price": 990})
        assert res.status == "success" and seen == {"offer_id": "OF1", "price": 990.0}
    _run(go())


def test_yandex_capability_not_supported(monkeypatch):
    called = []
    async def fake(**k): called.append(1); return {}
    monkeypatch.setattr(action_catalog.wb_client, "set_discount", fake)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        c = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="yandex",
                                  status="connected", scopes=["prices"])
        db.add(c)
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=c.id, scope="prices",
                             secret_enc=credential_vault.encrypt("t")))
        await db.flush()
        res = await executor.execute(db=db, user_id=uid, action_type="reduce_discount",
                                     payload={"marketplace": "yandex", "offer_id": "x", "discount": 3},
                                     decision_id="dY")
        assert res.status == "rejected"
        assert res.error["code"] == ExecutionError.CAPABILITY_NOT_SUPPORTED
        assert called == []  # client never called
        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == uid))).scalars().first()
        assert log.status == "rejected" and log.decision_id == "dY"
    _run(go())


# ── measurement binding ──────────────────────────────────────────────────────

def test_reduce_discount_measured_on_net_profit():
    # under margin_crisis, problem binding wins → net_profit (never revenue)
    assert target_metric("reduce_discount", problem_type="margin_crisis") == "net_profit"
    assert target_metric("set_price", problem_type="margin_crisis") == "net_profit"


def test_reduce_discount_in_measurable_allowlist():
    assert "reduce_discount" in emb._MEASURABLE_ACTIONS
    assert emb._ACTION_SCOPE["reduce_discount"] == "prices"


# ── declarative margin action space ──────────────────────────────────────────

def test_margin_action_space():
    space = problem_action_space("margin_crisis")
    assert "set_price" in space and "reduce_discount" in space
    assert problem_action_space("pricing_problem") == ()


# ── reverter ─────────────────────────────────────────────────────────────────

def test_reverter_wb_restores_old_discount():
    spec = action_catalog.get("reduce_discount")
    action, inv = spec.reverter(
        {"marketplace": "wildberries", "offer_id": "1", "discount": 5, "old_discount": 12}, {})
    assert action == "reduce_discount" and inv["discount"] == 12

def test_reverter_ozon_restores_old_price():
    spec = action_catalog.get("reduce_discount")
    action, inv = spec.reverter(
        {"marketplace": "ozon", "offer_id": "1", "price": 990, "old_price": 1200}, {})
    assert inv["price"] == 1200
