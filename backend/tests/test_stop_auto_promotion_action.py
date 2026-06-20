"""
Sprint A3 — stop_auto_promotion margin action (mirrors A2 reduce_discount).
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from services.marketplace import executor, credential_vault, action_catalog
from services.marketplace.errors import ExecutionError
from services.marketplace.action_metric_binding import target_metric, problem_action_space
from services import capability_registry
from services import execution_measurement_bridge as emb
from services.insight_decision_bridge import emit_candidates


def _run(c): return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _conn(db, uid, mp):
    c = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              status="connected", scopes=["promotions"])
    db.add(c)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=c.id, scope="promotions",
                         secret_enc=credential_vault.encrypt("t")))
    await db.flush()


def test_registration():
    spec = action_catalog.get("stop_auto_promotion")
    assert spec.action_type == "stop_auto_promotion"
    assert spec.required_scope == "promotions"
    assert spec.reversible is True and spec.reverter is not None


def test_capability_matrix():
    assert executor.capability_for_action("stop_auto_promotion") == "promotions.write"
    assert capability_registry.verdict("promotions.write", "wb") == "api"
    assert capability_registry.verdict("promotions.write", "ozon") == "api"
    assert capability_registry.verdict("promotions.write", "yandex") == "impossible"


def test_wb_execution(monkeypatch):
    seen = {}
    async def fake(*, token, offer_id, enabled):
        seen.update(offer_id=offer_id, enabled=enabled); return {"requestId": "wb1"}
    monkeypatch.setattr(action_catalog.wb_client, "set_auto_promotion", fake)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, "wildberries")
        res = await executor.execute(db=db, user_id=uid, action_type="stop_auto_promotion",
                                     payload={"marketplace": "wildberries", "offer_id": "123"})
        assert res.status == "success" and seen == {"offer_id": "123", "enabled": False}
    _run(go())


def test_ozon_execution(monkeypatch):
    seen = {}
    async def fake(*, token, client_id, offer_id, enabled):
        seen.update(offer_id=offer_id, enabled=enabled); return {"requestId": "oz1"}
    monkeypatch.setattr(action_catalog.ozon_client, "set_auto_promotion", fake)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, "ozon")
        res = await executor.execute(db=db, user_id=uid, action_type="stop_auto_promotion",
                                     payload={"marketplace": "ozon", "offer_id": "OF1"})
        assert res.status == "success" and seen == {"offer_id": "OF1", "enabled": False}
    _run(go())


def test_yandex_rejected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, "yandex")
        res = await executor.execute(db=db, user_id=uid, action_type="stop_auto_promotion",
                                     payload={"marketplace": "yandex", "offer_id": "x"},
                                     decision_id="dY")
        assert res.status == "rejected"
        assert res.error["code"] == ExecutionError.CAPABILITY_NOT_SUPPORTED
        log = (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == uid))).scalars().first()
        assert log.status == "rejected" and log.decision_id == "dY"
    _run(go())


def test_action_space_emission():
    keys = {c.action_key for c in emit_candidates("margin_crisis:wb:SKU1")}
    assert keys == {"set_price", "reduce_discount", "stop_auto_promotion"}
    assert "stop_auto_promotion" in problem_action_space("margin_crisis")


def test_net_profit_binding():
    assert target_metric("stop_auto_promotion", problem_type="margin_crisis") == "net_profit"


def test_measurable_allowlist():
    assert "stop_auto_promotion" in emb._MEASURABLE_ACTIONS
    assert emb._ACTION_SCOPE["stop_auto_promotion"] == "promotions"


def test_reverter_reenables():
    spec = action_catalog.get("stop_auto_promotion")
    action, inv = spec.reverter(
        {"marketplace": "wildberries", "offer_id": "1", "enabled": False, "old_enabled": True}, {})
    assert action == "stop_auto_promotion" and inv["enabled"] is True
