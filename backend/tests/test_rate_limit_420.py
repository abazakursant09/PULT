"""YANDEX-PRE-1 — HTTP 420 "Enhance Your Calm" is a rate limit, not a plain 4xx.

Yandex Market throttles with 420 instead of 429. Before this, 420 fell through the generic 4xx branch
and surfaced as MARKETPLACE_4XX — which the review-sync loop classifies as a PRODUCT-local error, so
the cursor advanced and the batch kept calling a marketplace that had just asked us to stop. That is
the exact failure mode fixed for 429 in AR-AUTO-FILL, and it was still open for 420.

These tests pin both halves: the HTTP taxonomy in base_client, and the batch-level consequence in the
review-sync loop. The 429 / 4xx / auth paths are pinned too, because base_client is shared with
Wildberries and Ozon and their behaviour must not move.
"""
import asyncio
import uuid

import httpx
import pytest

from services.marketplace.base_client import BaseMarketplaceClient, _RETRYABLE_STATUS
from services.marketplace.errors import ExecutionError

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


class _FakeResponse:
    """Minimal stand-in for httpx.Response — only what _handle_response reads."""

    def __init__(self, status_code: int, body: bytes = b""):
        self.status_code = status_code
        self.content = body
        self.text = body.decode() if body else ""

    def json(self):
        raise ValueError("not json")


def _client(statuses):
    """A client whose transport returns the given statuses in order, counting the calls."""
    client = BaseMarketplaceClient("https://mp.example", max_retries=2)
    calls = {"n": 0}
    seq = list(statuses)

    class _Ctx:
        async def __aenter__(self_inner):
            return self_inner

        async def __aexit__(self_inner, *exc):
            return False

        async def request(self_inner, *a, **kw):
            calls["n"] += 1
            code = seq[min(calls["n"] - 1, len(seq) - 1)]
            return _FakeResponse(code)

    return client, calls, _Ctx


def _call(client, ctx_factory, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: ctx_factory())
    # keep the test fast — the real backoff sleeps between attempts
    monkeypatch.setattr(BaseMarketplaceClient, "_backoff", staticmethod(lambda attempt: asyncio.sleep(0)))
    return _run(client.request("POST", "/x", token="t"))


# ── HTTP taxonomy ────────────────────────────────────────────────────────────
def test_420_is_retryable():
    assert 420 in _RETRYABLE_STATUS


def test_420_retries_then_raises_rate_limit(monkeypatch):
    client, calls, ctx = _client([420])
    with pytest.raises(ExecutionError) as e:
        _call(client, ctx, monkeypatch)
    assert e.value.code == ExecutionError.RATE_LIMIT          # not MARKETPLACE_4XX
    assert e.value.retryable is True
    assert calls["n"] == 3                                    # first attempt + 2 retries


def test_420_retried_exactly_like_429(monkeypatch):
    c420, calls420, ctx420 = _client([420])
    c429, calls429, ctx429 = _client([429])
    for c, ctx in ((c420, ctx420), (c429, ctx429)):
        with pytest.raises(ExecutionError):
            _call(c, ctx, monkeypatch)
    assert calls420["n"] == calls429["n"]


def test_420_that_recovers_returns_normally(monkeypatch):
    """A throttle that clears on retry is a success, not an error — same as 429."""
    client, calls, ctx = _client([420, 200])
    out = _call(client, ctx, monkeypatch)
    assert out == {}                                          # empty 200 body
    assert calls["n"] == 2


def test_429_still_rate_limit(monkeypatch):
    client, _calls, ctx = _client([429])
    with pytest.raises(ExecutionError) as e:
        _call(client, ctx, monkeypatch)
    assert e.value.code == ExecutionError.RATE_LIMIT


@pytest.mark.parametrize("status", [400, 404, 422])
def test_plain_4xx_stays_marketplace_4xx(status, monkeypatch):
    client, calls, ctx = _client([status])
    with pytest.raises(ExecutionError) as e:
        _call(client, ctx, monkeypatch)
    assert e.value.code == ExecutionError.MARKETPLACE_4XX
    assert calls["n"] == 1                                    # never retried


@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses_unchanged(status, monkeypatch):
    client, calls, ctx = _client([status])
    with pytest.raises(ExecutionError) as e:
        _call(client, ctx, monkeypatch)
    assert e.value.code == ExecutionError.AUTH
    assert calls["n"] == 1


def test_5xx_unchanged(monkeypatch):
    client, _calls, ctx = _client([500])
    with pytest.raises(ExecutionError) as e:
        _call(client, ctx, monkeypatch)
    assert e.value.code == ExecutionError.MARKETPLACE_5XX


# ── Batch-level consequence: a 420 stops the connection, like any rate limit ──
from contextlib import asynccontextmanager                            # noqa: E402
from datetime import datetime                                         # noqa: E402

from sqlalchemy import select                                         # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy.orm import sessionmaker                               # noqa: E402
from sqlalchemy.pool import StaticPool                                # noqa: E402

from database import Base                                             # noqa: E402
import models  # noqa: F401,E402  register tables
from models.user import User                                          # noqa: E402
from models.product import Product                                    # noqa: E402
from models.automation_rule import AutomationRule                     # noqa: E402
from models.marketplace_connection import MarketplaceConnection       # noqa: E402
from models.api_credential import ApiCredential                       # noqa: E402
from config import settings as _settings                              # noqa: E402
from services.marketplace import credential_vault                     # noqa: E402
from services.marketplace.reviews import REVIEW_PROVIDERS             # noqa: E402
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV  # noqa: E402
import tasks.auto_review_pipeline as pipe                             # noqa: E402


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, marketplace="wildberries", products=1):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=["feedbacks"],
                                 ozon_client_id="CID" if marketplace == "ozon" else None)
    db.add(conn)
    await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    for _ in range(products):
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="SKU", marketplace=marketplace))
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                          action_type="publish_review_response", mode="auto", enabled=True,
                          guard={}, connection_id=conn.id,
                          consent_at=datetime.utcnow(), consent_version=_CV))
    await db.commit()
    return uid


def _bind(monkeypatch, db):
    @asynccontextmanager
    async def _factory():
        yield db
    monkeypatch.setattr(pipe, "AsyncSessionLocal", _factory)


def test_420_stops_the_sync_batch_for_that_connection(monkeypatch):
    """A throttled marketplace gets no further calls this cycle — the untouched tail waits."""
    db = _run(_new_db())
    _run(_seed(db, products=20))
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind(monkeypatch, db)

    calls = {"n": 0}
    async def _fetch(token, product_ref):
        calls["n"] += 1
        # what base_client raises for a 420 after its retries are spent
        raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _fetch)

    out = _run(pipe.run_auto_sync_reviews())

    assert calls["n"] == 1                                    # the other 19 were never requested
    assert out["stopped"] == 1
    conn = _run(db.execute(select(MarketplaceConnection))).scalars().first()
    assert conn.review_sync_fail_count == 1                   # existing backoff engaged
    assert conn.review_sync_next_at is not None
    assert (conn.review_sync_next_at - datetime.utcnow()).total_seconds() > 0
    assert conn.review_sync_cursor is None                    # nothing processed → nothing skipped


def test_420_on_one_connection_does_not_stop_another(monkeypatch):
    db = _run(_new_db())
    _run(_seed(db, marketplace="wildberries", products=3))
    _run(_seed(db, marketplace="ozon", products=1))
    _bind(monkeypatch, db)

    async def _throttled(token, product_ref):
        raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
    ozon_calls = {"n": 0}
    async def _ok(token, product_ref):
        ozon_calls["n"] += 1
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _throttled)
    monkeypatch.setattr(REVIEW_PROVIDERS["ozon"], "fetch_reviews", _ok)

    _run(pipe.run_auto_sync_reviews())

    assert ozon_calls["n"] == 1                               # the healthy store kept working
    conns = {c.marketplace: c for c in
             _run(db.execute(select(MarketplaceConnection))).scalars().all()}
    assert conns["wildberries"].review_sync_fail_count == 1
    assert conns["ozon"].review_sync_fail_count == 0


def test_plain_4xx_does_not_stop_the_batch(monkeypatch):
    """The contrast that makes the fix matter: a real product-level 4xx still lets the batch run."""
    db = _run(_new_db())
    _run(_seed(db, products=5))
    monkeypatch.setattr(_settings, "review_sync_product_batch_size", 20)
    _bind(monkeypatch, db)

    calls = {"n": 0}
    async def _fetch(token, product_ref):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExecutionError(ExecutionError.MARKETPLACE_4XX, "404: no such product")
        return []
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _fetch)

    out = _run(pipe.run_auto_sync_reviews())

    assert calls["n"] == 5 and out["stopped"] == 0
    conn = _run(db.execute(select(MarketplaceConnection))).scalars().first()
    assert conn.review_sync_fail_count == 0                   # connection is healthy, no backoff
