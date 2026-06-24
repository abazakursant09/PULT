"""
A2.2-pre-b.4 — Ozon campaign state control (real execution path).

Ozon ad_set_state now executes through the Performance API with an OAuth bearer
(resolved + cached from the advert_performance credential), replacing the honesty
guard. start→activate, pause→deactivate. 401 → invalidate + refresh + retry ONCE.
WB path unchanged; marketplace isolation preserved (Ozon never calls the WB client).
"""
import asyncio
import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog          # noqa: F401
from models.automation_rule import AutomationRule      # noqa: F401
from services.marketplace import executor, credential_vault
from services.marketplace import ozon_performance_auth as auth
from services.marketplace import action_catalog
from services.marketplace.ozon_client import ozon_client
from services.marketplace.wb_client import wb_client
from services.marketplace.errors import ExecutionError

NOW = datetime(2026, 6, 24, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _ozon_conn(db, *, with_perf=True):
    uid = str(uuid.uuid4())
    scopes = ["advert_performance"] if with_perf else ["advert"]
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                 status="connected", scopes=scopes, ozon_client_id="cid")
    db.add(conn); await db.flush()
    if with_perf:
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope="advert_performance",
                             secret_enc=credential_vault.encrypt("secret"),
                             meta={"client_id": "perf-id"}))
    await db.commit()
    return uid, conn.id


def _transport(captured, *, status=200, body=None):
    body = body if body is not None else {"access_token": "BEARER", "expires_in": 1800}

    def handler(request):
        captured.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


# ── (1)(2) activate / deactivate request shape ───────────────────────────────

def test_activate_request_shape(monkeypatch):
    cap = []
    async def fake_request(method, path, *, token, auth_header):
        cap.append((method, path, token, auth_header)); return {"requestId": "rq"}
    monkeypatch.setattr(ozon_client._performance(), "request", fake_request)
    r = _run(ozon_client.set_campaign_state(token="B", campaign_id=5, action="activate"))
    assert r == {"requestId": "rq"}
    assert cap == [("POST", "/api/client/campaign/5/activate", "Bearer B", "Authorization")]


def test_deactivate_request_shape(monkeypatch):
    cap = []
    async def fake_request(method, path, *, token, auth_header):
        cap.append(path); return {}
    monkeypatch.setattr(ozon_client._performance(), "request", fake_request)
    _run(ozon_client.set_campaign_state(token="B", campaign_id=9, action="deactivate"))
    assert cap == ["/api/client/campaign/9/deactivate"]


# ── (6) unsupported action validation ────────────────────────────────────────

def test_unsupported_action_rejected():
    err = None
    try:
        _run(ozon_client.set_campaign_state(token="B", campaign_id=1, action="frobnicate"))
    except ExecutionError as e:
        err = e
    assert err is not None and err.code == ExecutionError.VALIDATION


# ── (3)(4) bearer acquisition + cache reuse ──────────────────────────────────

def test_bearer_acquisition_and_cache_reuse():
    async def go():
        db = await _db()
        _, conn_id = await _ozon_conn(db)
        auth.cache().invalidate(conn_id)
        cap = []
        tr = _transport(cap)
        b1 = await auth.acquire_bearer(db, connection_id=conn_id,
                                       base_url="https://perf.test", transport=tr, now=NOW)
        b2 = await auth.acquire_bearer(db, connection_id=conn_id,
                                       base_url="https://perf.test", transport=tr, now=NOW)
        assert b1 == "BEARER" and b2 == "BEARER"
        assert len(cap) == 1                      # second call reused the cached bearer
    _run(go())


# ── (5) cache invalidate + refresh on 401 (dispatch retries once) ────────────

def test_dispatch_refreshes_bearer_on_401(monkeypatch):
    async def go():
        db = await _db()
        _, conn_id = await _ozon_conn(db)
        bearers = []
        async def fake_acquire(_db, *, connection_id, force_refresh=False, **kw):
            bearers.append(force_refresh); return f"B{len(bearers)}"
        monkeypatch.setattr(auth, "acquire_bearer", fake_acquire)

        attempts = []
        async def fake_set(*, token, campaign_id, action):
            attempts.append(token)
            if len(attempts) == 1:
                raise ExecutionError(ExecutionError.AUTH, "auth rejected (401)")
            return {"requestId": "ok"}
        monkeypatch.setattr(ozon_client, "set_campaign_state", fake_set)

        ctx = {"marketplace": "ozon", "db": db, "connection_id": conn_id}
        res = await action_catalog._dispatch_set_state_ozon(
            {"campaign_id": 7, "action": "pause"}, ctx)
        assert res["state"] == "pause"
        assert bearers == [False, True]           # first normal, then forced refresh
        assert attempts == ["B1", "B2"]           # retried exactly once, no loop
    _run(go())


def test_dispatch_does_not_retry_non_auth_error(monkeypatch):
    async def go():
        db = await _db()
        _, conn_id = await _ozon_conn(db)
        async def fake_acquire(_db, *, connection_id, force_refresh=False, **kw):
            return "B"
        monkeypatch.setattr(auth, "acquire_bearer", fake_acquire)
        calls = {"n": 0}
        async def fake_set(*, token, campaign_id, action):
            calls["n"] += 1
            raise ExecutionError(ExecutionError.MARKETPLACE_4XX, "400")
        monkeypatch.setattr(ozon_client, "set_campaign_state", fake_set)
        err = None
        try:
            await action_catalog._dispatch_set_state_ozon(
                {"campaign_id": 7, "action": "start"}, {"marketplace": "ozon", "db": db,
                                                         "connection_id": conn_id})
        except ExecutionError as e:
            err = e
        assert err.code == ExecutionError.MARKETPLACE_4XX and calls["n"] == 1   # no retry
    _run(go())


# ── (7) credential missing → controlled error ────────────────────────────────

def test_credential_missing_controlled_error():
    async def go():
        db = await _db()
        _, conn_id = await _ozon_conn(db, with_perf=False)   # no advert_performance cred
        err = None
        try:
            await auth.acquire_bearer(db, connection_id=conn_id,
                                      base_url="https://perf.test", transport=_transport([]))
        except ExecutionError as e:
            err = e
        assert err is not None and err.code == ExecutionError.MISSING_SCOPE
    _run(go())


# ── (8) end-to-end via executor + marketplace isolation ──────────────────────

def test_executor_ozon_success_isolated_from_wb(monkeypatch):
    async def go():
        db = await _db()
        uid, conn_id = await _ozon_conn(db)
        monkeypatch.setattr(auth, "acquire_bearer",
                            lambda _db, **kw: _coro("BEARER"))
        seen = []
        async def fake_set(*, token, campaign_id, action):
            seen.append((token, campaign_id, action)); return {"requestId": "rq"}
        monkeypatch.setattr(ozon_client, "set_campaign_state", fake_set)
        # any WB campaign call would be an isolation violation
        def boom(**_):
            raise AssertionError("Ozon dispatch must not call the WB client")
        monkeypatch.setattr(wb_client, "set_campaign_state", boom)

        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "ozon", "campaign_id": 7, "action": "pause"})
        assert res.status == "success" and res.result["state"] == "pause"
        assert seen == [("BEARER", 7, "deactivate")]   # pause → deactivate, real bearer
    _run(go())


# ── (9) WB path unchanged ────────────────────────────────────────────────────

def test_wb_ad_set_state_unchanged(monkeypatch):
    async def go():
        db = await _db()
        uid = str(uuid.uuid4())
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["advert"])
        db.add(conn); await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="advert",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
        await db.commit()
        async def fake(*, token, campaign_id, action):
            return {"requestId": f"rq-{action}"}
        monkeypatch.setattr(wb_client, "set_campaign_state", fake)
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "wildberries", "campaign_id": 7, "action": "pause"})
        assert res.status == "success" and res.result["state"] == "pause"
    _run(go())


async def _coro(v):
    return v
