"""
PULT-LAUNCH-2.1A — containment of the unconfirmed auto-promotion exit.

Proves the P1 fix: PULT must NEVER send an ordinary-promotion command or an unconfirmed
endpoint and call the result an auto-promotion exit. `stop_auto_promotion` promised an
automatic EXIT from an auto-promotion that no wired provider path delivers:
  * WB   /api/v1/promotions/participation — unconfirmed against the official API;
  * Ozon /v1/actions/products/deactivate  — ORDINARY-action opt-out, not Hot Sale / auto;
  * Yandex — no participation write API.

So the action is fail-closed at the executor (before any connection/token/dispatch), at the
provider dispatch (defense-in-depth), and at the Feed/binding capability gate. The diagnostic
signal stays; it just never becomes an executable Decision. Zero marketplace calls, ever.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.execution_log import ExecutionLog
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.operations_signal import OperationsSignal
from models.decision import Decision

from services.marketplace import credential_vault, executor
from services.decision_outcome.decision_bridge import (
    capability_supported, bridge_links_to_decisions, SKIPPED_NO_CAPABILITY,
)
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY as OPS_SIGNAL
from services import capability_registry
import services.marketplace.wb_client as wb_mod
import services.marketplace.ozon_client as ozon_mod

CONTAINED = "stop_auto_promotion"


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _tripwire(monkeypatch):
    """Any provider participation call is a containment BREACH → blow up loudly."""
    async def boom_wb(*a, **k):
        raise AssertionError("wb_client.set_auto_promotion was called — containment breached")

    async def boom_ozon(*a, **k):
        raise AssertionError("ozon_client.set_auto_promotion was called — containment breached")

    monkeypatch.setattr(wb_mod.wb_client, "set_auto_promotion", boom_wb)
    monkeypatch.setattr(ozon_mod.ozon_client, "set_auto_promotion", boom_ozon)


async def _connected(db, uid, marketplace, *, ozon_client_id=None):
    cn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                               status="connected", scopes=["promotions", "prices"],
                               ozon_client_id=ozon_client_id)
    db.add(cn)
    await db.flush()
    for scope in ("promotions", "prices"):
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=cn.id, scope=scope,
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return cn


async def _count_status(db, action_type, status):
    return (await db.execute(select(func.count()).select_from(ExecutionLog).where(
        ExecutionLog.action_type == action_type, ExecutionLog.status == status))).scalar()


# ── 1. WB stop_auto_promotion → honest unsupported, 0 provider calls ──────────

def test_wb_contained_zero_calls(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _connected(db, uid, "wildberries")
        res = await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                     payload={"marketplace": "wildberries", "offer_id": "123"})
        assert res.status == "rejected" and not res.ok
        assert res.error["code"] == "CAPABILITY_NOT_SUPPORTED"
        assert "Wildberries" in res.error["detail"] and "не поддерживается" in res.error["detail"]
        assert await _count_status(db, CONTAINED, "success") == 0
    _run(go())


# ── 2. WB revert of an OLD saved action → 0 unconfirmed calls, no false revert ─

def test_wb_revert_of_old_action_zero_calls(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # a pre-2.1A success row, exactly as history would hold it
        old = ExecutionLog(user_id=uid, action_type=CONTAINED, marketplace="wildberries",
                           mode="manual_l3", payload={"marketplace": "wildberries",
                                                       "offer_id": "123", "enabled": False,
                                                       "old_enabled": True},
                           status="success", api_request_id="wb-old")
        db.add(old); await db.commit(); await db.refresh(old)

        res = await executor.revert(db=db, user_id=uid, log_id=old.id)
        assert res.status == "rejected" and not res.ok        # inverse rejected
        await db.refresh(old)
        assert old.status == "success"                        # NEVER a false "reverted"
    _run(go())


# ── 3. Ozon stop_auto_promotion → never ordinary deactivate, 0 calls ──────────

def test_ozon_contained_no_ordinary_deactivate(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _connected(db, uid, "ozon", ozon_client_id="cid")
        res = await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                     payload={"marketplace": "ozon", "offer_id": "SKU1"})
        assert res.status == "rejected" and not res.ok
        assert "Ozon" in res.error["detail"]
        assert await _count_status(db, CONTAINED, "success") == 0
    _run(go())


# ── 4. Yandex stays unsupported ───────────────────────────────────────────────

def test_yandex_unsupported(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                     payload={"marketplace": "yandex", "offer_id": "SKU1"})
        assert res.status == "rejected" and not res.ok
    _run(go())
    assert capability_supported(CONTAINED, "yandex") is False


# ── 5. hand-built / stale payload cannot bypass the server guard ──────────────

def test_manual_payload_cannot_bypass_guard(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _connected(db, uid, "wildberries")
        # an attacker-shaped payload with everything "valid" still fail-closes
        res = await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                     payload={"marketplace": "wildberries", "offer_id": "999",
                                              "enabled": False, "extra": "x"},
                                     mode="manual_l3", idempotency_key="k-manual")
        assert res.status == "rejected" and not res.ok
        assert await _count_status(db, CONTAINED, "success") == 0
    _run(go())


# ── 6. Decision Feed offers no executable false button ────────────────────────

def test_feed_no_false_button(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        db.add(EngineSignalDecisionLink(user_id=uid, contour="operations",
               signal_table="operations_signal", signal_id="sig1",
               insight_key=f"{OPS_SIGNAL}:ozon:SKU1", action_key=CONTAINED,
               decision_id=None, link_status="proposed", marketplace="ozon", sku="SKU1"))
        await db.commit()
        # the bridge never promotes a contained action → no executable Decision, no Apply button
        r = await bridge_links_to_decisions(db, user_id=uid)
        assert r.promoted == 0 and r.skipped == 1
        assert r.items[0].outcome == SKIPPED_NO_CAPABILITY
        assert (await db.execute(select(func.count()).select_from(Decision))).scalar() == 0
    _run(go())
    # both gates agree the action is not executable on any marketplace
    assert capability_supported(CONTAINED, "wildberries") is False
    assert capability_supported(CONTAINED, "ozon") is False


# ── 7. the diagnostic notification survives without an auto action ────────────

def test_diagnostic_signal_survives(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        sig = await build_operations_signal(db, user_id=uid, marketplace="ozon", sku="SKU1",
                                            net_profit=-100.0, in_auto_promotion=True)
        await db.commit()
        assert sig is not None and sig.status == "active"
        # the diagnostic text advises a MANUAL action — it never claims PULT will do it
        assert "Остановить участие товара" in sig.what_to_do
        rows = (await db.execute(select(OperationsSignal))).scalars().all()
        assert len(rows) == 1
    _run(go())


# ── 8. no false-success ExecutionLog for the contained action ────────────────

def test_no_false_success_log(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _connected(db, uid, "wildberries")
        for i in range(3):
            await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                   payload={"marketplace": "wildberries", "offer_id": str(i)},
                                   idempotency_key=f"k{i}")
        assert await _count_status(db, CONTAINED, "success") == 0
        assert await _count_status(db, CONTAINED, "rejected") == 3
    _run(go())


# ── 9. set_price and other confirmed actions are NOT broken ──────────────────

def test_confirmed_actions_not_broken(monkeypatch):
    calls = []

    async def fake_set_price(*, token, offer_id, price, discount=None):
        calls.append((offer_id, price)); return {"requestId": "wb-price"}

    monkeypatch.setattr(wb_mod.wb_client, "set_price", fake_set_price)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _connected(db, uid, "wildberries")
        res = await executor.execute(db=db, user_id=uid, action_type="set_price",
                                     payload={"marketplace": "wildberries", "offer_id": "123",
                                              "price": 1500})
        assert res.ok and res.status == "success"
        assert calls == [("123", 1500)]
    _run(go())
    assert capability_supported("set_price", "wildberries") is True
    assert capability_supported("reduce_discount", "wildberries") is True


# ── 10. existing history stays readable ──────────────────────────────────────

def test_existing_history_readable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        old = ExecutionLog(user_id=uid, action_type=CONTAINED, marketplace="ozon",
                           mode="manual_l3", payload={"offer_id": "SKU1"}, status="success",
                           api_request_id="hist-1", created_at=datetime(2026, 6, 1))
        db.add(old); await db.commit()
        rows = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.action_type == CONTAINED))).scalars().all()
        assert len(rows) == 1 and rows[0].status == "success"   # untouched, still readable
    _run(go())


# ── 11. honest Russian text, no promise of an automatic exit ─────────────────

def test_honest_capability_text(monkeypatch):
    _tripwire(monkeypatch)

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await executor.execute(db=db, user_id=uid, action_type=CONTAINED,
                                     payload={"marketplace": "wildberries", "offer_id": "1"})
        detail = res.error["detail"]
        assert "пока не поддерживается" in detail and "вручную" in detail
        # no wording that promises PULT performs the exit
        assert "PULT отключит" not in detail and "отключим" not in detail
    _run(go())

    # registry (source of truth for §6.1/§19) is honest for every marketplace
    for mp in ("wb", "ozon", "yandex"):
        av = capability_registry.availability("promotions.write", mp)
        assert av["available"] is False and av["reason"]
