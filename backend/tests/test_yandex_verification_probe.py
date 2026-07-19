"""
F1.2d — the Yandex Market credential probe adapter.

Grounded in the official Partner API docs: auth is a bare `Api-Key` header (OAuth is
explicitly deprecated), and `POST /v2/auth/token` returns the ACCESS GROUPS the presented
token holds — Yandex's answer to Ozon's `/v1/roles`. That introspection call is what makes
per-scope verification possible with the scope vocabulary we already have; without it we
could only prove a token is alive, which would have forced either a new scope literal or a
false claim that a token grants `prices` when nobody checked.

Two Yandex-specific traps, both proven here:

  * throttling is **420 Enhance Your Calm**, not 429 — a client that only knows 429
    silently mishandles it;
  * there is **no `Retry-After`** — the reset is an RFC-822 *timestamp* in
    `X-RateLimit-Resource-Until`, so it is parsed defensively and never crashes.

`errors[].message` is never parsed: it is prose, not a contract. 401 (credentials dead) and
403 (credentials alive, under-permissioned) are cleanly separated by the docs, and that
split is all we need.

Every HTTP call is mocked. No test touches a real Yandex API.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

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

from routers.connections import create_connection
from schemas.marketplace import ConnectionCreate
from services.marketplace import credential_vault
from services.marketplace.verification import runner
from services.marketplace.verification.adapters import get_adapter
from services.marketplace.verification.adapters.base import ProbeContext, ProbeResponse
from services.marketplace.verification.adapters.yandex import YandexProbeAdapter
from services.marketplace.verification.transport import ProbeTransportError
from services.marketplace.verification.taxonomy import VerificationOutcome as O

TOKEN = "yandex-api-key-secret"

# Identity fields Yandex would expose elsewhere — none of them may ever be stored.
BUSINESS_ID = "987654"
CAMPAIGN_ID = "112233"


def _groups(*names):
    return {"authScopes": list(names)}


class FakeTransport:
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


def _verify(scope, *responses):
    t = FakeTransport(*responses)
    ctx = ProbeContext(secret=TOKEN, marketplace="yandex", scope=scope)
    return asyncio.run(YandexProbeAdapter().verify(ctx, t)), t


# ── A. registry + transport shape ───────────────────────────────────────────

def test_yandex_is_registered_and_selected_by_lookup():
    assert isinstance(get_adapter("yandex"), YandexProbeAdapter)
    assert get_adapter("YANDEX") is not None


def test_api_key_header_is_bare_with_no_bearer_prefix():
    """OAuth is deprecated at Yandex; the token IS the Api-Key header."""
    _result, t = _verify("prices", _resp(200, _groups("pricing")))
    assert t.requests[0].headers == {"Api-Key": TOKEN}
    assert "Bearer" not in str(t.requests[0].headers)
    assert "Authorization" not in t.requests[0].headers


def test_only_the_token_introspection_endpoint_is_called():
    _result, t = _verify("prices", _resp(200, _groups("pricing")))
    assert t.urls == ["https://api.partner.market.yandex.ru/v2/auth/token"]
    assert t.methods == ["POST"]
    assert len(t.requests) == 1


# ── B. scope → access-group mapping ─────────────────────────────────────────

@pytest.mark.parametrize("scope,group", [
    ("prices", "pricing"),
    ("prices", "pricing:read-only"),
    ("content", "offers-and-cards-management"),
    ("content", "offers-and-cards-management:read-only"),
    ("stocks", "inventory-and-order-processing"),
    ("stocks", "inventory-and-order-processing:read-only"),
    ("promotions", "promotion"),
    ("promotions", "promotion:read-only"),
])
def test_scope_is_verified_by_its_own_access_group(scope, group):
    result, _t = _verify(scope, _resp(200, _groups(group)))
    assert result.outcome is O.VERIFIED


@pytest.mark.parametrize("scope", ["prices", "content", "stocks", "promotions"])
@pytest.mark.parametrize("group", ["all-methods", "all-methods:read-only"])
def test_all_methods_groups_satisfy_every_supported_scope(scope, group):
    result, _t = _verify(scope, _resp(200, _groups(group)))
    assert result.outcome is O.VERIFIED


def test_a_group_for_another_scope_does_not_verify_this_one():
    """A pricing token proves nothing about stocks — that is the point of per-scope state."""
    result, _t = _verify("stocks", _resp(200, _groups("pricing", "promotion")))
    assert result.outcome is O.MISSING_SCOPE
    assert result.outcome is not O.INVALID_CREDENTIALS


def test_empty_group_list_is_missing_scope_not_invalid():
    result, _t = _verify("prices", _resp(200, {"authScopes": []}))
    assert result.outcome is O.MISSING_SCOPE


# ── C. status classification ────────────────────────────────────────────────

def test_401_is_invalid_credentials():
    result, _t = _verify("prices", _resp(401, {"status": "ERROR", "errors": [
        {"code": "UNAUTHORIZED", "message": "Api-Key token is invalid"}]}))
    assert result.outcome is O.INVALID_CREDENTIALS


def test_403_is_missing_scope_not_a_dead_token():
    """403 means the credentials are ALIVE but under-permissioned. Documented, and distinct."""
    result, _t = _verify("prices", _resp(403, {"status": "ERROR", "errors": [
        {"code": "FORBIDDEN", "message": "Token does not have any of the scopes"}]}))
    assert result.outcome is O.MISSING_SCOPE
    assert result.outcome is not O.INVALID_CREDENTIALS


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_marketplace_unavailable(status):
    result, _t = _verify("prices", _resp(status))
    assert result.outcome is O.MARKETPLACE_UNAVAILABLE


def test_timeout_is_classified_by_the_spine():
    t = FakeTransport(ProbeTransportError(ProbeTransportError.TIMEOUT))
    ctx = ProbeContext(secret=TOKEN, marketplace="yandex", scope="prices")
    with pytest.raises(ProbeTransportError):
        asyncio.run(YandexProbeAdapter().verify(ctx, t))   # the adapter does not swallow it


# ── D. 420, not 429 ─────────────────────────────────────────────────────────

def test_420_is_rate_limited():
    """Yandex throttles with 420 Enhance Your Calm — a 429-only client mishandles it."""
    result, _t = _verify("prices", _resp(420))
    assert result.outcome is O.RATE_LIMITED


def test_429_is_not_special_cased_here():
    """WB's 429 logic must not have been copied in: Yandex does not use 429 for throttling."""
    result, _t = _verify("prices", _resp(429))
    assert result.outcome is not O.RATE_LIMITED      # falls through to the unknown-4xx branch
    assert result.outcome is O.ADAPTER_ERROR


def test_until_header_is_parsed_into_seconds():
    until = datetime.now(timezone.utc) + timedelta(seconds=90)
    result, _t = _verify("prices", _resp(420, None, {
        "x-ratelimit-resource-until": format_datetime(until),
        "x-ratelimit-resource-limit": "1000",
        "x-ratelimit-resource-remaining": "0",
    }))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is not None
    assert 60 <= result.retry_after_seconds <= 95     # RFC-822 has second granularity


@pytest.mark.parametrize("headers", [
    {},                                                   # no headers at all
    {"x-ratelimit-resource-until": "not-a-date"},         # unparseable
    {"x-ratelimit-resource-until": ""},                   # empty
    {"retry-after": "30"},                                # Yandex sends none — must be ignored
])
def test_missing_or_malformed_rate_limit_headers_do_not_crash(headers):
    result, _t = _verify("prices", _resp(420, None, headers))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is None


def test_a_reset_time_in_the_past_yields_no_negative_wait():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    result, _t = _verify("prices", _resp(420, None, {
        "x-ratelimit-resource-until": format_datetime(past)}))
    assert result.retry_after_seconds is None


# ── E. schema ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    None,
    {"unexpected": True},
    {"authScopes": "not-a-list"},
    {"authScopes": [1, 2, 3]},
    [],
])
def test_unrecognised_body_is_schema_mismatch_never_a_verdict(body):
    """Inventing a verdict from a payload we cannot read would accuse a healthy token."""
    result, _t = _verify("prices", _resp(200, body))
    assert result.outcome is O.SCHEMA_MISMATCH
    assert result.outcome is not O.VERIFIED
    assert result.outcome is not O.MISSING_SCOPE


def test_a_nested_result_envelope_is_also_accepted():
    result, _t = _verify("prices", _resp(200, {"result": {"authScopes": ["pricing"]}}))
    assert result.outcome is O.VERIFIED


# ── F. unsupported scopes ───────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["advert"])
def test_unsupported_scopes_perform_no_http_call(scope):
    """Yandex has no `advert` group at all.

    Asserting a mapping we picked ourselves is the exact failure this contour exists to
    prevent, so we say we cannot check rather than guess. `feedbacks` left this list once the
    documented owner of goods-feedback (the `communication` group) was confirmed.
    """
    result, t = _verify(scope)              # nothing scripted: any call would fail
    assert result.outcome is O.VERIFICATION_UNSUPPORTED
    assert t.requests == []


# ── F2. feedbacks: verifiable, but only with a WRITE-capable grant ──────────

def test_feedbacks_verified_with_communication_group():
    result, _t = _verify("feedbacks", _resp(200, {"authScopes": ["communication"]}))
    assert result.outcome is O.VERIFIED


def test_feedbacks_verified_with_all_methods():
    result, _t = _verify("feedbacks", _resp(200, {"authScopes": ["all-methods"]}))
    assert result.outcome is O.VERIFIED


def test_feedbacks_rejects_a_read_only_grant():
    """Auto Reviews PUBLISHES a reply. A read-only key cannot, however much it looks like it
    covers reviews — verifying it would defer the real failure to the first publish, where it
    would read as a marketplace error rather than the missing permission it is."""
    result, _t = _verify("feedbacks", _resp(200, {"authScopes": ["all-methods:read-only"]}))
    assert result.outcome is O.MISSING_SCOPE


def test_advert_is_not_silently_treated_as_promotion():
    result, _t = _verify("advert")
    assert result.outcome is not O.VERIFIED


# ── G. no retries ───────────────────────────────────────────────────────────

def test_the_adapter_never_retries():
    for resp in (_resp(420), _resp(500), _resp(401)):
        _result, t = _verify("prices", resp)
        assert len(t.requests) == 1


# ── H. end to end: audit, secrets, identity ─────────────────────────────────

async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _yandex_connection(db, *, scope="prices"):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id, marketplace="yandex",
                             identity_status="unverified")
    db.add(acc)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace="yandex", status="connected",
        scopes=[scope], workspace_id=ws.id, marketplace_account_id=acc.id,
    )
    db.add(conn)
    cred = ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                         secret_enc=credential_vault.encrypt(TOKEN))
    db.add(cred)
    await db.commit()
    return user, conn, acc, cred


def test_end_to_end_verified_leaks_no_secret_and_writes_no_identity(caplog):
    async def go():
        db = await _orm_session()
        user, conn, acc, _cred = await _yandex_connection(db)
        # a body that ALSO carries identity data — none of it may be stored
        body = {"authScopes": ["all-methods:read-only"],
                "business": {"id": BUSINESS_ID}, "campaigns": [{"id": CAMPAIGN_ID}]}
        t = FakeTransport(_resp(200, body))

        with caplog.at_level("DEBUG"):
            _c, credential, result = await runner.verify_credential(
                db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.VERIFIED
        await db.refresh(credential)
        await db.refresh(conn)
        await db.refresh(acc)

        assert credential.verification_status == "verified"
        assert conn.verification_status == "verified"
        assert conn.status == "connected"

        # identity discovery is a LATER slice — nothing here may touch it
        assert acc.external_account_id is None
        assert acc.identity_status == "unverified"

        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        row = str(attempt.__dict__)
        for leak in (TOKEN, BUSINESS_ID, CAMPAIGN_ID):
            assert leak not in row, f"{leak} reached the audit"
        assert attempt.probe_key == "yandex.auth_token.prices"

        assert TOKEN not in caplog.text
        assert BUSINESS_ID not in caplog.text
    asyncio.run(go())


def test_end_to_end_timeout_preserves_a_verified_credential():
    async def go():
        db = await _orm_session()
        user, conn, _acc, cred = await _yandex_connection(db)
        cred.verification_status = "verified"
        cred.verified_at = datetime.utcnow()
        await db.commit()

        t = FakeTransport(ProbeTransportError(ProbeTransportError.TIMEOUT))
        _c, credential, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.TIMEOUT
        await db.refresh(credential)
        assert credential.verification_status == "verified", \
            "a timeout destroyed a proven Yandex credential"
    asyncio.run(go())


# ── I. connection enablement ────────────────────────────────────────────────

def test_post_connections_accepts_yandex_and_stores_it_unverified():
    """Saving is still not verifying: no marketplace is called when a credential is stored."""
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="y@b.com", name="Y", hashed_password="x")
        db.add(user)
        db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=user.id,
                         created_at=datetime.utcnow()))
        await db.commit()

        out = await create_connection(
            ConnectionCreate(marketplace="yandex", token=TOKEN, scope="prices"), user, db)

        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        cred = (await db.execute(sa.select(ApiCredential))).scalar_one()
        account = (await db.execute(sa.select(MarketplaceAccount))).scalar_one()

        assert conn.marketplace == "yandex"
        assert conn.status == "connected"
        assert conn.verification_status == "unverified"   # nothing was checked
        assert cred.verification_status == "unverified"
        assert credential_vault.decrypt(cred.secret_enc) == TOKEN

        assert account.marketplace == "yandex"
        assert account.external_account_id is None
        assert account.identity_status != "verified"

        # storing a credential must not fabricate a verification attempt
        assert (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalars().all() == []

        assert out.verification_status == "unverified"
        assert [s.scope for s in out.scopes_verification] == ["prices"]
    asyncio.run(go())
