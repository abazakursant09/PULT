"""
Action Space A1 — executor consults the capability registry on the write path.

Mapped actions are gated: unsupported (verdict impossible/None) → rejected before
any client dispatch, with an explicit CAPABILITY_NOT_SUPPORTED and decision_id
provenance preserved. Supported → unchanged execution. Unmapped actions
(set_price/update_card) skip the gate (legacy behavior, no regression).
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
from services.marketplace import executor, credential_vault
from services.marketplace.action_catalog import ActionSpec
from services.marketplace.errors import ExecutionError


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _conn(db, uid, mp="wb", scope="advert"):
    c = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              status="connected", scopes=[scope])
    db.add(c)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=c.id, scope=scope,
                         secret_enc=credential_vault.encrypt("tok")))
    await db.flush()
    return c


def _stub_spec(calls, *, scope="advert"):
    async def disp(token, payload, ctx):
        calls.append("dispatched")
        return {"api_request_id": "r1"}
    return ActionSpec(action_type="x", marketplace=None, required_scope=scope,
                      validate=lambda p: None, dispatch=disp, reversible=False)


# ── mapping (pure) ───────────────────────────────────────────────────────────

def test_capability_for_action_mapping():
    assert executor.capability_for_action("ad_set_bid") == "campaign_control"
    assert executor.capability_for_action("ad_set_state") == "campaign_control"
    assert executor.capability_for_action("publish_review_response") == "review_reply"
    # no registry write-capability key → unmapped (legacy allow)
    assert executor.capability_for_action("set_price") is None
    assert executor.capability_for_action("update_card") is None
    assert executor.capability_for_action("totally_unknown") is None


# ── supported capability → executes as before ────────────────────────────────

def test_supported_capability_dispatches(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.action_catalog, "get", lambda a: _stub_spec(calls))
    monkeypatch.setattr(executor, "capability_for_action", lambda a: "campaign_control")  # api on wb

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid)
        res = await executor.execute(db=db, user_id=uid, action_type="ad_set_bid",
                                     payload={"marketplace": "wb"}, decision_id="dec-1")
        assert res.status == "success"
        assert calls == ["dispatched"]
    _run(go())


# ── unsupported capability → rejected, no dispatch ───────────────────────────

def test_unsupported_capability_rejected_before_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.action_catalog, "get", lambda a: _stub_spec(calls))
    # auto_promotions is 'impossible' on wb in the registry
    monkeypatch.setattr(executor, "capability_for_action", lambda a: "auto_promotions")

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid)
        res = await executor.execute(db=db, user_id=uid, action_type="some_promo_action",
                                     payload={"marketplace": "wb"}, decision_id="dec-9")
        assert res.status == "rejected"
        assert res.error and res.error["code"] == ExecutionError.CAPABILITY_NOT_SUPPORTED
        assert calls == []  # client never called
        # rejected ExecutionLog persisted with decision_id provenance
        log = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.user_id == uid))).scalars().first()
        assert log is not None and log.status == "rejected" and log.decision_id == "dec-9"
    _run(go())


def test_unsupported_dry_run_rejected_no_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.action_catalog, "get", lambda a: _stub_spec(calls))
    monkeypatch.setattr(executor, "capability_for_action", lambda a: "auto_promotions")

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid)
        res = await executor.execute(db=db, user_id=uid, action_type="x",
                                     payload={"marketplace": "wb"}, dry_run=True)
        assert res.status == "rejected"
        assert calls == []
    _run(go())


# ── unmapped action → gate skipped (legacy) ──────────────────────────────────

def test_unmapped_action_skips_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.action_catalog, "get", lambda a: _stub_spec(calls))
    # real capability_for_action: set_price → None → gate skipped → dispatch

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _conn(db, uid, scope="advert")
        res = await executor.execute(db=db, user_id=uid, action_type="set_price",
                                     payload={"marketplace": "wb"})
        assert res.status == "success"
        assert calls == ["dispatched"]
    _run(go())


# ── guard: registry consulted on write path ──────────────────────────────────

def test_executor_consults_capability_registry_on_write():
    import inspect
    src = inspect.getsource(executor.execute)
    assert "capability_registry.verdict(" in src
    assert "capability_for_action(" in src
