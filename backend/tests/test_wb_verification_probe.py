"""
F1.2b-b — the Wildberries probe adapter.

Grounded in WB's official OpenAPI spec: `GET <category-host>/ping` is documented as the
check for token validity AND token-category match (read-only, 200/401/429, 3 req/30 s), and
`GET common-api/api/v1/seller-info` accepts a token of ANY category.

The case that matters most is the 401. WB answers every rejection with 401 — invalid,
expired, revoked and wrong-category are indistinguishable, and the only differentiator is a
free-text `detail` WB does not enumerate. So a bare 401 must NOT become
`invalid_credentials`: that would tell a seller their perfectly good "Цены и скидки" token
is broken merely because it was checked against the feedbacks host. And when the
discriminating seller-info call cannot be made — rate-limited (1/min, 1 per 24 h for Basic
tokens) or down — the adapter must refuse to guess and return a temporary outcome.

Every HTTP call here is mocked. No test ever touches a real Wildberries API.
"""
import asyncio
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_credential import ApiCredential
from models.connection_verification_attempt import ConnectionVerificationAttempt
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace

from services.marketplace import credential_vault
from services.marketplace.verification import runner
from services.marketplace.verification.adapters.base import ProbeContext, ProbeResponse
from services.marketplace.verification.adapters.wildberries import WildberriesProbeAdapter
from services.marketplace.verification.transport import ProbeTransportError
from services.marketplace.verification.taxonomy import VerificationOutcome as O

TOKEN = "wb-t0ken-secret"
PING_OK = {"TS": "2026-07-12T11:19:05+03:00", "Status": "OK"}
SELLER_INFO_OK = {"name": "ООО Ромашка", "sid": "1a2b3c4d", "tin": "7701234567",
                  "tradeMark": "Ромашка"}


class FakeTransport:
    """Records every request; replies from a queued script. Never touches the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, req):
        self.requests.append(req)
        if not self.responses:
            raise AssertionError("the adapter made more requests than the test scripted")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def urls(self):
        return [r.url for r in self.requests]

    @property
    def methods(self):
        return [r.method for r in self.requests]


def _resp(status, body=None, headers=None):
    return ProbeResponse(status=status, headers=headers or {}, json=body)


def _ctx(scope="prices"):
    return ProbeContext(secret=TOKEN, marketplace="wildberries", scope=scope)


def _verify(scope, *responses):
    t = FakeTransport(*responses)
    result = asyncio.run(WildberriesProbeAdapter().verify(_ctx(scope), t))
    return result, t


# ── A. scope → host mapping, read-only ──────────────────────────────────────

@pytest.mark.parametrize("scope,host", [
    ("feedbacks", "https://feedbacks-api.wildberries.ru/ping"),
    ("prices",    "https://discounts-prices-api.wildberries.ru/ping"),
    ("advert",    "https://advert-api.wildberries.ru/ping"),
    ("content",   "https://content-api.wildberries.ru/ping"),
    ("stocks",    "https://marketplace-api.wildberries.ru/ping"),
])
def test_scope_maps_to_its_category_host(scope, host):
    result, t = _verify(scope, _resp(200, PING_OK))
    assert result.outcome is O.VERIFIED
    assert t.urls == [host]
    assert t.methods == ["GET"]          # read-only, always


def test_probe_only_ever_issues_reads():
    for scope in ("feedbacks", "prices", "advert", "content", "stocks"):
        _result, t = _verify(scope, _resp(200, PING_OK))
        assert set(t.methods) == {"GET"}
        assert all(u.endswith("/ping") for u in t.urls)


def test_token_is_sent_raw_in_the_authorization_header():
    _result, t = _verify("prices", _resp(200, PING_OK))
    assert t.requests[0].headers == {"Authorization": TOKEN}   # no "Bearer" — WB apiKey scheme


# ── B. status classification ────────────────────────────────────────────────

def test_documented_success_shape_is_required_for_verified():
    result, _t = _verify("prices", _resp(200, PING_OK))
    assert result.outcome is O.VERIFIED
    assert result.response_schema_status == "ok"


def test_a_200_with_an_unrecognised_body_is_not_verified():
    """Claiming `verified` from a body we do not recognise would be a guess."""
    result, _t = _verify("prices", _resp(200, {"unexpected": True}))
    assert result.outcome is O.SCHEMA_MISMATCH
    assert result.outcome is not O.VERIFIED

    result, _t = _verify("prices", _resp(200, None))
    assert result.outcome is O.SCHEMA_MISMATCH


def test_429_is_rate_limited_and_parses_wb_retry_header():
    result, _t = _verify("prices", _resp(429, None, {"x-ratelimit-retry": "17"}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds == 17


def test_missing_or_broken_rate_limit_headers_do_not_crash():
    """WB documents these headers in prose but omits them from the OpenAPI specs."""
    result, _t = _verify("prices", _resp(429, None, {}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is None

    result, _t = _verify("prices", _resp(429, None, {"x-ratelimit-retry": "soon"}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is None


def test_402_is_tariff_restricted():
    result, _t = _verify("prices", _resp(402))
    assert result.outcome is O.TARIFF_RESTRICTED


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_marketplace_unavailable(status):
    result, _t = _verify("prices", _resp(status))
    assert result.outcome is O.MARKETPLACE_UNAVAILABLE


# ── C. the 401 discrimination — the heart of the adapter ────────────────────

def test_a_bare_401_never_becomes_invalid_credentials():
    """It triggers the seller-info check instead — WB will not say why it rejected."""
    result, t = _verify("prices", _resp(401), _resp(200, SELLER_INFO_OK))
    assert result.outcome is not O.INVALID_CREDENTIALS
    assert len(t.requests) == 2
    assert t.urls[1] == "https://common-api.wildberries.ru/api/v1/seller-info"


def test_401_then_seller_info_ok_is_missing_scope():
    """The token is real — so the /ping 401 was about the CATEGORY, not the token."""
    result, t = _verify("feedbacks", _resp(401), _resp(200, SELLER_INFO_OK))
    assert result.outcome is O.MISSING_SCOPE
    assert t.methods == ["GET", "GET"]


def test_401_then_seller_info_401_is_invalid_credentials():
    result, _t = _verify("prices", _resp(401), _resp(401))
    assert result.outcome is O.INVALID_CREDENTIALS


def test_401_then_seller_info_429_is_temporary_not_an_accusation():
    """We could not find out. Accusing the seller on no evidence is not an option."""
    result, _t = _verify("prices", _resp(401), _resp(429, None, {"x-ratelimit-retry": "60"}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds == 60
    assert result.outcome is not O.INVALID_CREDENTIALS


@pytest.mark.parametrize("status", [500, 503])
def test_401_then_seller_info_unavailable_is_temporary(status):
    result, _t = _verify("prices", _resp(401), _resp(status))
    assert result.outcome is O.MARKETPLACE_UNAVAILABLE
    assert result.outcome is not O.INVALID_CREDENTIALS


def test_seller_info_is_not_called_on_a_healthy_probe():
    """Its budget (1/min; 1 per 24 h for a Basic token) is spent only when needed."""
    _result, t = _verify("prices", _resp(200, PING_OK))
    assert len(t.requests) == 1
    assert "seller-info" not in t.urls[0]


# ── D. promotions ───────────────────────────────────────────────────────────

def test_promotions_makes_no_network_call_and_is_unsupported():
    """WB does not document which token category owns our `promotions`. We will not guess."""
    result, t = _verify("promotions")          # no scripted responses => none may be used
    assert result.outcome is O.VERIFICATION_UNSUPPORTED
    assert t.requests == []


def test_promotions_is_not_silently_treated_as_prices():
    result, t = _verify("promotions")
    assert result.outcome is not O.VERIFIED
    assert not any("discounts-prices" in u for u in t.urls)


# ── E. no retries ───────────────────────────────────────────────────────────

def test_the_adapter_never_retries():
    """WB allows 3 /ping per 30 s per host — a retry is how a rate limit becomes a block."""
    _result, t = _verify("prices", _resp(429, None, {}))
    assert len(t.requests) == 1

    _result, t = _verify("prices", _resp(500))
    assert len(t.requests) == 1


# ── F. end to end through the runner: secrets, audit, identity ──────────────

async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _wb_connection(db, scope="prices"):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id,
                             marketplace="wildberries", identity_status="unverified")
    db.add(acc)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace="wildberries",
        status="connected", scopes=[scope], workspace_id=ws.id,
        marketplace_account_id=acc.id,
    )
    db.add(conn)
    cred = ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                         secret_enc=credential_vault.encrypt(TOKEN))
    db.add(cred)
    await db.commit()
    return user, conn, acc, cred


def test_verified_probe_updates_only_that_credential_and_leaves_identity_alone(caplog):
    async def go():
        db = await _orm_session()
        user, conn, acc, cred = await _wb_connection(db)
        t = FakeTransport(_resp(200, PING_OK))

        with caplog.at_level("DEBUG"):
            _c, credential, result = await runner.verify_credential(
                db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.VERIFIED
        await db.refresh(credential)
        await db.refresh(conn)
        await db.refresh(acc)

        assert credential.verification_status == "verified"
        assert credential.verified_at is not None
        assert conn.verification_status == "verified"      # its only stored scope is proven
        assert conn.status == "connected"                  # execution gate untouched

        # identity discovery is NOT part of this slice
        assert acc.external_account_id is None
        assert acc.identity_status == "unverified"

        # the token appears nowhere in the audit, and no response body is stored
        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.outcome == "verified"
        assert attempt.probe_key == "wb.ping.prices"
        assert attempt.http_status == 200
        assert TOKEN not in str(attempt.__dict__)
        for value in attempt.__dict__.values():
            assert SELLER_INFO_OK["sid"] != value

        # ...nor in the logs
        assert TOKEN not in caplog.text
    asyncio.run(go())


def test_seller_info_body_is_never_persisted():
    async def go():
        db = await _orm_session()
        user, conn, acc, _cred = await _wb_connection(db)
        t = FakeTransport(_resp(401), _resp(200, SELLER_INFO_OK))

        _c, _cred2, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.MISSING_SCOPE
        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        row = str(attempt.__dict__)
        for leak in (SELLER_INFO_OK["sid"], SELLER_INFO_OK["tin"], SELLER_INFO_OK["name"],
                     TOKEN):
            assert leak not in row

        await db.refresh(acc)
        assert acc.external_account_id is None     # discovery is a later slice
    asyncio.run(go())


def test_transport_timeout_becomes_a_temporary_outcome_and_preserves_state():
    async def go():
        db = await _orm_session()
        user, conn, _acc, cred = await _wb_connection(db)
        cred.verification_status = "verified"
        cred.verified_at = datetime.utcnow()
        await db.commit()

        t = FakeTransport(ProbeTransportError(ProbeTransportError.TIMEOUT))
        _c, credential, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.TIMEOUT
        await db.refresh(credential)
        assert credential.verification_status == "verified", \
            "a timeout destroyed a proven credential"
    asyncio.run(go())
