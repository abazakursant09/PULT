"""
A2.2-pre-b.2 — Ozon Performance OAuth token exchange + TTL bearer cache.

Adapter-spine seam only: token exchange, in-process cache, credential resolver.
No campaign endpoints, no executor wiring, no binding. Secrets (client_secret,
access_token) never appear in repr / error output.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.api_credential import ApiCredential
from services.marketplace import credential_vault
from services.marketplace import ozon_performance_auth as auth
from services.marketplace.ozon_performance_auth import (
    get_ozon_performance_token, OzonPerformanceTokenCache, OzonPerformanceToken,
    resolve_performance_credential, PERFORMANCE_SCOPE,
)
from services.marketplace.errors import ExecutionError

NOW = datetime(2026, 6, 24, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


def _transport(captured: list, *, status=200, body=None):
    body = body if body is not None else {"access_token": "BEARER-XYZ", "expires_in": 1800}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


# ── (1) successful token exchange ────────────────────────────────────────────

def test_token_exchange_success():
    tok = _run(get_ozon_performance_token(
        client_id="cid", client_secret="sec",
        base_url="https://perf.test", transport=_transport([]), now=NOW))
    assert isinstance(tok, OzonPerformanceToken)
    assert tok.access_token == "BEARER-XYZ"
    assert tok.expires_in == 1800
    assert tok.expires_at == NOW + timedelta(seconds=1800)


# ── (2) request payload carries client_id, client_secret, grant_type ─────────

def test_request_payload_contains_oauth_fields():
    cap: list = []
    _run(get_ozon_performance_token(
        client_id="my-id", client_secret="my-secret",
        base_url="https://perf.test", transport=_transport(cap), now=NOW))
    assert len(cap) == 1
    req = cap[0]
    assert req.url.path == "/api/client/token"
    sent = json.loads(req.content)
    assert sent == {"client_id": "my-id", "client_secret": "my-secret",
                    "grant_type": "client_credentials"}


def test_token_exchange_rejected_maps_to_auth_without_body():
    err = None
    try:
        _run(get_ozon_performance_token(
            client_id="cid", client_secret="topsecret",
            base_url="https://perf.test",
            transport=_transport([], status=401, body={"echo": "topsecret"}), now=NOW))
    except ExecutionError as e:
        err = e
    assert err is not None and err.code == ExecutionError.AUTH
    assert "topsecret" not in str(err)          # body (and any secret) never surfaced


# ── (3)(4) TTL cache reuse + refresh ─────────────────────────────────────────

def test_cache_reuses_unexpired_token():
    async def go():
        cache = OzonPerformanceTokenCache()
        calls = {"n": 0}

        async def fetch():
            calls["n"] += 1
            return OzonPerformanceToken("T1", 1800, NOW + timedelta(seconds=1800))

        t1 = await cache.get_token(key="conn1", fetch=fetch, now=NOW)
        # 10 min later — still well within the 1800s lifetime → reuse, no refetch
        t2 = await cache.get_token(key="conn1", fetch=fetch, now=NOW + timedelta(minutes=10))
        assert t1 is t2 and calls["n"] == 1
    _run(go())


def test_cache_refreshes_expired_token():
    async def go():
        cache = OzonPerformanceTokenCache()
        calls = {"n": 0}

        async def fetch():
            calls["n"] += 1
            return OzonPerformanceToken(f"T{calls['n']}", 1800,
                                        (NOW if calls["n"] == 1 else NOW + timedelta(hours=1))
                                        + timedelta(seconds=1800))

        await cache.get_token(key="conn1", fetch=fetch, now=NOW)
        # past expiry (margin included) → refetch
        t2 = await cache.get_token(key="conn1", fetch=fetch, now=NOW + timedelta(seconds=1800))
        assert calls["n"] == 2 and t2.access_token == "T2"
    _run(go())


def test_cache_invalidate_forces_refetch():
    async def go():
        cache = OzonPerformanceTokenCache()
        calls = {"n": 0}

        async def fetch():
            calls["n"] += 1
            return OzonPerformanceToken("T", 1800, NOW + timedelta(seconds=1800))

        await cache.get_token(key="c", fetch=fetch, now=NOW)
        cache.invalidate("c")
        await cache.get_token(key="c", fetch=fetch, now=NOW)
        assert calls["n"] == 2
    _run(go())


# ── (5) missing credential → controlled error ────────────────────────────────

async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def test_missing_credential_controlled_error():
    async def go():
        db = await _db()
        err = None
        try:
            await resolve_performance_credential(db, connection_id="nope")
        except ExecutionError as e:
            err = e
        assert err is not None and err.code == ExecutionError.MISSING_SCOPE
    _run(go())


def test_credential_without_client_id_controlled_error():
    async def go():
        db = await _db()
        conn_id = str(uuid.uuid4())
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn_id,
                             scope=PERFORMANCE_SCOPE,
                             secret_enc=credential_vault.encrypt("sec"), meta={}))  # no client_id
        await db.commit()
        err = None
        try:
            await resolve_performance_credential(db, connection_id=conn_id)
        except ExecutionError as e:
            err = e
        assert err is not None and err.code == ExecutionError.MISSING_SCOPE
    _run(go())


def test_credential_resolves_client_id_and_secret():
    async def go():
        db = await _db()
        conn_id = str(uuid.uuid4())
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn_id,
                             scope=PERFORMANCE_SCOPE,
                             secret_enc=credential_vault.encrypt("super-secret"),
                             meta={"client_id": "perf-123"}))
        await db.commit()
        cred = await resolve_performance_credential(db, connection_id=conn_id)
        assert cred.client_id == "perf-123" and cred.client_secret == "super-secret"
    _run(go())


# ── (6) secrets never exposed in repr ────────────────────────────────────────

def test_token_repr_hides_bearer():
    tok = OzonPerformanceToken("BEARER-XYZ", 1800, NOW)
    r = repr(tok)
    assert "BEARER-XYZ" not in r and "***" in r


def test_credential_repr_hides_secret():
    cred = auth.PerformanceCredential(client_id="cid", client_secret="super-secret")
    r = repr(cred)
    assert "super-secret" not in r and "***" in r


# ── (7) no schema change: ApiCredential reused, no new table/columns ─────────

def test_no_new_schema_reuses_apicredential():
    cols = {c.name for c in ApiCredential.__table__.columns}
    # exact existing column set — the slice adds NONE
    assert cols == {"id", "connection_id", "scope", "secret_enc", "meta",
                    "expires_at", "created_at", "updated_at"}
    # advert_performance is only a scope VALUE, not a new model
    assert PERFORMANCE_SCOPE == "advert_performance"
