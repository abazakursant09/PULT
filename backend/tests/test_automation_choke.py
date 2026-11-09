"""SECURITY-2D-1A — central fail-closed automation_enabled choke inside the executor.

An AUTOMATED (L4) action requires settings.automation_enabled to be exactly True; the check lives inside
services/marketplace/executor.execute(), before any connection / token / capability / guard / idempotency
/ dispatch. So NO caller (scheduler, task, decision_apply, a direct internal call, an automated retry, or
a revert of an automated action — which re-enters execute() with the ORIGINAL stored mode) can reach a
provider while automation is off. Manual L3 is not gated by this flag. The provider stubs COUNT actual
dispatch calls, so every "0 calls" assertion is about real dispatches, not just ExecutionLog rows.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
import models  # noqa: F401  register tables
from config import settings
from services.marketplace import executor, credential_vault, operation_key
from services.marketplace.wb_client import wb_client

AUTOMATION_CODE = "GUARD_AUTOMATION_DISABLED"


@pytest.mark.parametrize("mode", ["manual_l3", "automated_l4", "unexpected_mode"])
@pytest.mark.parametrize("flag", [True, None, 0, "false"])
def test_read_only_rejects_every_execution_mode_before_access(monkeypatch, mode, flag):
    monkeypatch.setattr(executor, "settings", SimpleNamespace(
        automation_enabled=True, marketplace_read_only=flag))
    class NoDatabaseAccess:
        def __getattr__(self, name):
            raise AssertionError(f"dry-run accessed database: {name}")
    result = _run(executor.execute(db=NoDatabaseAccess(), user_id="synthetic",
        action_type="publish_review_response", payload=_review_payload(), mode=mode, dry_run=True))
    assert result.status == "rejected"
    assert result.error["code"] == "GUARD_MARKETPLACE_READ_ONLY"


def test_read_only_audits_manual_rejection_without_credentials(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "marketplace_read_only", True)
        async def forbidden(*args, **kwargs):
            raise AssertionError("read-only reached credential resolution")
        monkeypatch.setattr(executor, "_resolve_connection", forbidden)
        try:
            result = await executor.execute(db=db, user_id=uid,
                action_type="publish_review_response", payload=_review_payload(), mode="manual_l3")
            record = await db.get(ExecutionLog, result.log_id)
            assert record.status == "rejected"
            assert record.error_code == "GUARD_MARKETPLACE_READ_ONLY"
            assert record.dispatch_started_at is None
            return result
        finally:
            await db.close()
    result = _run(go())
    assert result.error["code"] == "GUARD_MARKETPLACE_READ_ONLY"


def test_read_only_blocks_manual_inverse_provider_call(monkeypatch):
    async def go():
        db, uid, conn_id = await _setup()
        log_id = await _seed_price_log(db, uid, conn_id, mode="manual_l3")
        monkeypatch.setattr(settings, "marketplace_read_only", True)
        counter = {"calls": 0}
        monkeypatch.setattr(wb_client, "set_price", _counting_setprice(counter))
        try:
            result = await executor.revert(db=db, user_id=uid, log_id=log_id)
            assert counter["calls"] == 0
            return result
        finally:
            await db.close()
    result = _run(go())
    assert result.error["code"] == "GUARD_MARKETPLACE_READ_ONLY"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                 status="connected", scopes=["feedbacks", "prices"])
    db.add(conn)
    await db.flush()
    for sc in ("feedbacks", "prices"):
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=sc,
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid, conn.id


def _counting_publish(counter):
    async def _fake(*, token, feedback_id, text):
        counter["calls"] += 1
        return {"api_request_id": "req-1"}
    return _fake


def _counting_setprice(counter):
    async def _fake(*, token, offer_id, price, discount=None):
        counter["calls"] += 1
        return {"api_request_id": "px-1"}
    return _fake


def _review_payload():
    return {"marketplace": "wildberries", "feedback_id": "fb1", "text": "Спасибо!",
            "rating": 5, "safety_category": "SAFE"}


# ══ core: automated L4 blocked when the flag is not True ═════════════════════

def test_automated_l4_rejected_when_flag_false(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="automated_l4",
                                     rule={"enabled": True})
        return res, c["calls"]
    res, calls = _run(go())
    assert res.status == "rejected" and not res.ok
    assert res.error["code"] == AUTOMATION_CODE
    assert calls == 0                                # NO provider dispatch


def test_automated_l4_reaches_dispatch_once_when_flag_true(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", True)
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="automated_l4",
                                     rule={"enabled": True},
                                     idempotency_key=operation_key.review_key("f5aba5f8-25df-4364-8461-ac3d3145c7c0"))
        return res, c["calls"]
    res, calls = _run(go())
    assert calls == 1                                # flag True → the choke passes, one real dispatch
    assert res.error is None or res.error.get("code") != AUTOMATION_CODE


def test_non_true_config_value_fails_closed(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", "true")   # truthy but NOT the bool True
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="automated_l4",
                                     rule={"enabled": True})
        return res, c["calls"]
    res, calls = _run(go())
    assert res.error["code"] == AUTOMATION_CODE and calls == 0   # `is not True` → fail-closed


def test_api_data_sync_enabled_does_not_substitute(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        monkeypatch.setattr(settings, "api_data_sync_enabled", True)   # a different flag — must NOT unlock
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="automated_l4",
                                     rule={"enabled": True})
        return res, c["calls"]
    res, calls = _run(go())
    assert res.error["code"] == AUTOMATION_CODE and calls == 0


# ══ manual L3 is NOT gated by this flag ══════════════════════════════════════

def test_manual_l3_not_blocked_by_choke(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="manual_l3",   # user-initiated L3
                                     idempotency_key=operation_key.review_key("f5aba5f8-25df-4364-8461-ac3d3145c7c0"))
        return res, c["calls"]
    res, calls = _run(go())
    assert res.error is None or res.error.get("code") != AUTOMATION_CODE   # choke did NOT fire
    assert calls == 1                                                      # L3 proceeds to dispatch


# ══ dry-run: automated L4 + flag off → HONEST non-executable, 0 dispatch ═════

def test_automated_dry_run_flag_false_is_honest_rejected(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        res = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                     payload=_review_payload(), mode="automated_l4",
                                     rule={"enabled": True}, dry_run=True)
        return res, c["calls"]
    res, calls = _run(go())
    assert res.status == "rejected" and res.error["code"] == AUTOMATION_CODE   # never a green preview
    assert calls == 0


# ══ no caller can bypass: internal / retry route through execute() ═══════════

def test_internal_and_retry_callers_cannot_bypass(monkeypatch):
    """decision_apply, the scheduler tick, and any automated retry all funnel through this same
    execute() entry point (services/marketplace/__init__ forbids direct client calls), so a direct
    execute(mode='automated_l4') stands in for every internal/retry caller."""
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _counting_publish(c)
        # first call (the "automated action") and a "retry" — both blocked, both 0 dispatch
        r1 = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                    payload=_review_payload(), mode="automated_l4", rule={"enabled": True})
        r2 = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                    payload=_review_payload(), mode="automated_l4", rule={"enabled": True})
        return r1, r2, c["calls"]
    r1, r2, calls = _run(go())
    assert r1.error["code"] == AUTOMATION_CODE and r2.error["code"] == AUTOMATION_CODE
    assert calls == 0


# ══ revert provenance: original mode is stored on ExecutionLog.mode ══════════

async def _seed_price_log(db, uid, conn_id, *, mode):
    rec = ExecutionLog(id=str(uuid.uuid4()), user_id=uid, connection_id=conn_id,
                       action_type="set_price", marketplace="wildberries", mode=mode,
                       payload={"marketplace": "wildberries", "offer_id": "o1", "price": 100,
                                "old_price": 120}, status="success", api_request_id="orig")
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec.id


def test_revert_of_automated_action_flag_false_zero_calls(monkeypatch):
    async def go():
        db, uid, conn_id = await _setup()
        log_id = await _seed_price_log(db, uid, conn_id, mode="automated_l4")   # provenance = L4
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.set_price = _counting_setprice(c)
        res = await executor.revert(db=db, user_id=uid, log_id=log_id)   # re-enters execute(mode=L4)
        return res, c["calls"]
    res, calls = _run(go())
    assert res.status == "rejected" and res.error["code"] == AUTOMATION_CODE
    assert calls == 0                                # the inverse never reached the provider


def test_manual_revert_not_blocked(monkeypatch):
    async def go():
        db, uid, conn_id = await _setup()
        log_id = await _seed_price_log(db, uid, conn_id, mode="manual_l3")   # provenance = L3
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        wb_client.set_price = _counting_setprice(c)
        res = await executor.revert(db=db, user_id=uid, log_id=log_id)   # re-enters execute(mode=L3)
        return res, c["calls"]
    res, calls = _run(go())
    assert res.error is None or res.error.get("code") != AUTOMATION_CODE   # not blocked by the choke
    assert calls == 1                                                      # manual inverse dispatched


# ══ contained action stays contained (choke placed after the contained gate) ═

def test_stop_auto_promotion_contained_zero_calls(monkeypatch):
    async def go():
        db, uid, _ = await _setup()
        monkeypatch.setattr(settings, "automation_enabled", False)
        c = {"calls": 0}
        # any provider path would raise if called — proves 0 marketplace calls
        wb_client.set_price = _counting_setprice(c)
        res = await executor.execute(db=db, user_id=uid, action_type="stop_auto_promotion",
                                     payload={"marketplace": "wildberries", "offer_id": "o1"},
                                     mode="manual_l3")
        return res, c["calls"]
    res, calls = _run(go())
    assert res.status == "rejected" and calls == 0   # contained → 0 calls, unaffected by the choke
