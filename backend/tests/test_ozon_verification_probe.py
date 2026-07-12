"""
F1.2b-c — the Ozon credential probe adapter.

Grounded in Ozon's official Seller API spec: `POST /v1/seller/info` and `POST /v1/roles`
are both reads (Ozon uses POST for read operations — the verb is not the mutation), and
auth is a PAIR of headers, `Api-Key` + `Client-Id`.

Ozon is the mirror image of Wildberries, usefully so. WB answers every rejection with an
indistinguishable 401, so scope had to be *inferred* from a second call. Ozon states its
permissions outright via `/v1/roles`, so `missing_scope` is *observed*; and it documents
`Api-key is deactivated` separately from `Invalid Api-Key`, so `revoked` is a verdict we
can actually make.

Where Ozon says nothing, this adapter says nothing either:
  * the HTTP status of an auth failure is undocumented → classify off the message, not off
    a hard-coded 401;
  * a wrong Client-Id is indistinguishable from a wrong Api-Key → both are
    `invalid_credentials`;
  * an IP-restricted key is FINE and we are the ones who cannot use it → `adapter_error`,
    which accuses the seller of nothing.

Every HTTP call is mocked. No test touches a real Ozon API.
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
from services.marketplace.verification.adapters import get_adapter
from services.marketplace.verification.adapters.base import ProbeContext, ProbeResponse
from services.marketplace.verification.adapters.ozon import OzonProbeAdapter
from services.marketplace.verification.transport import ProbeTransportError
from services.marketplace.verification.taxonomy import VerificationOutcome as O

API_KEY = "ozon-api-key-secret"
CLIENT_ID = "123456"

SELLER_INFO_OK = {
    "company": {"inn": "7701234567", "ogrn": "1027700132195", "legal_name": "ООО Ромашка",
                "name": "Ромашка", "country": "RU", "currency": "RUB",
                "ownership_form": "LLC", "tax_system": "USN"},
    "ratings": [],
    "subscription": {"is_premium": False, "type": "basic"},
}
ROLES_WITH_PRICES = {"expires_at": "2027-01-18T09:54:23.296Z",
                     "roles": [{"name": "Admin", "methods": ["/v1/product", "/v1/actions"]}]}
ROLES_WITHOUT_PRICES = {"expires_at": "2027-01-18T09:54:23.296Z",
                        "roles": [{"name": "Posting FBS", "methods": ["/v1/posting"]}]}


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


def _ctx(scope="prices", client_id=CLIENT_ID):
    return ProbeContext(secret=API_KEY, marketplace="ozon", scope=scope,
                        ozon_client_id=client_id)


def _verify(scope="prices", *responses, client_id=CLIENT_ID):
    t = FakeTransport(*responses)
    result = asyncio.run(OzonProbeAdapter().verify(_ctx(scope, client_id), t))
    return result, t


def _err(message, status=403):
    return _resp(status, {"code": 7, "message": message, "details": []})


# ── A. registry ─────────────────────────────────────────────────────────────

def test_ozon_is_registered_and_selected_by_lookup():
    assert isinstance(get_adapter("ozon"), OzonProbeAdapter)
    assert get_adapter("OZON") is not None            # lookup is case-insensitive


# ── B. success path ─────────────────────────────────────────────────────────

def test_seller_info_then_roles_success_is_verified():
    result, t = _verify("prices", _resp(200, SELLER_INFO_OK), _resp(200, ROLES_WITH_PRICES))
    assert result.outcome is O.VERIFIED
    assert t.urls == ["https://api-seller.ozon.ru/v1/seller/info",
                      "https://api-seller.ozon.ru/v1/roles"]
    assert t.methods == ["POST", "POST"]      # POST, but read-only — Ozon reads with POST


def test_both_headers_are_sent():
    _result, t = _verify("prices", _resp(200, SELLER_INFO_OK), _resp(200, ROLES_WITH_PRICES))
    for req in t.requests:
        assert req.headers == {"Api-Key": API_KEY, "Client-Id": CLIENT_ID}


def test_promotions_scope_checks_the_actions_method():
    result, _t = _verify("promotions", _resp(200, SELLER_INFO_OK),
                         _resp(200, ROLES_WITH_PRICES))
    assert result.outcome is O.VERIFIED       # "/v1/actions" covers /v1/actions/products/...


def test_role_matching_accepts_an_exact_method_path_too():
    """Prefix matching is correct under both readings: an exact path is a prefix of itself."""
    exact = {"roles": [{"name": "R", "methods": ["/v1/product/import/prices"]}]}
    result, _t = _verify("prices", _resp(200, SELLER_INFO_OK), _resp(200, exact))
    assert result.outcome is O.VERIFIED


# ── C. capability ───────────────────────────────────────────────────────────

def test_missing_required_role_is_missing_scope():
    """Ozon states its permissions outright, so this is observed — not inferred."""
    result, _t = _verify("prices", _resp(200, SELLER_INFO_OK),
                         _resp(200, ROLES_WITHOUT_PRICES))
    assert result.outcome is O.MISSING_SCOPE
    assert result.outcome is not O.INVALID_CREDENTIALS


def test_missing_role_error_message_is_missing_scope():
    result, _t = _verify("prices", _err("Api-Key is missing a required role for a method"))
    assert result.outcome is O.MISSING_SCOPE


# ── D. credential failures ──────────────────────────────────────────────────

def test_invalid_api_key_message_is_invalid_credentials():
    result, _t = _verify("prices",
                         _err("Invalid Api-Key, please check the key and try again"))
    assert result.outcome is O.INVALID_CREDENTIALS


def test_deactivated_key_is_revoked_not_merely_invalid():
    """Ozon documents this separately from `Invalid Api-Key` — so we can say `revoked`."""
    result, _t = _verify(
        "prices", _err("Api-key is deactivated, use another one or generate a new one"))
    assert result.outcome is O.REVOKED
    assert result.outcome is not O.INVALID_CREDENTIALS


def test_missing_client_id_is_invalid_credentials_without_any_call():
    """Ozon authenticates with a PAIR. Half a credential is not a credential."""
    result, t = _verify("prices", client_id=None)     # nothing scripted: any call would fail
    assert result.outcome is O.INVALID_CREDENTIALS
    assert t.requests == []


def test_auth_failure_is_classified_by_message_not_by_a_guessed_status():
    """Ozon does not document the status of an auth failure — 401 and 403 both occur."""
    for status in (401, 403):
        result, _t = _verify("prices", _err("Invalid Api-Key", status=status))
        assert result.outcome is O.INVALID_CREDENTIALS


# ── E. IP restriction — the honest gap ──────────────────────────────────────

def test_ip_restriction_does_not_accuse_the_seller():
    """The key is fine; OUR address is not allowed. No outcome states that, so we do not
    pretend one does — `adapter_error` is temporary and blames nobody."""
    result, _t = _verify("prices", _err("Api-Key is restricted to specific IP addresses"))
    assert result.outcome is O.ADAPTER_ERROR
    assert result.outcome is not O.INVALID_CREDENTIALS
    assert result.outcome is not O.REVOKED
    assert result.outcome is not O.MISSING_SCOPE


# ── F. transport / rate limits ──────────────────────────────────────────────

def test_429_is_rate_limited_and_parses_item_retry_after():
    result, _t = _verify("prices", _resp(429, {"code": 8, "message": "RESOURCE_EXHAUSTED"},
                                         {"item-retry-after": "120"}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds == 120


def test_rate_limit_message_without_429_is_still_rate_limited():
    result, _t = _verify("prices", _err("You have reached request rate limit per second"))
    assert result.outcome is O.RATE_LIMITED


def test_missing_or_broken_retry_header_does_not_crash():
    result, _t = _verify("prices", _resp(429, None, {}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is None

    result, _t = _verify("prices", _resp(429, None, {"item-retry-after": "soon"}))
    assert result.outcome is O.RATE_LIMITED
    assert result.retry_after_seconds is None


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_marketplace_unavailable(status):
    result, _t = _verify("prices", _resp(status))
    assert result.outcome is O.MARKETPLACE_UNAVAILABLE


def test_the_adapter_never_retries():
    _result, t = _verify("prices", _resp(429, None, {}))
    assert len(t.requests) == 1

    _result, t = _verify("prices", _resp(500))
    assert len(t.requests) == 1


# ── G. schema ───────────────────────────────────────────────────────────────

def test_unrecognised_seller_info_body_is_schema_mismatch():
    result, _t = _verify("prices", _resp(200, {"unexpected": True}))
    assert result.outcome is O.SCHEMA_MISMATCH
    assert result.outcome is not O.VERIFIED

    result, _t = _verify("prices", _resp(200, None))
    assert result.outcome is O.SCHEMA_MISMATCH


def test_malformed_roles_payload_is_schema_mismatch_not_missing_scope():
    """A wrong `missing_scope` would accuse a seller whose key is perfectly fine."""
    for bad in ({"roles": "nonsense"},
                {"roles": [{"name": "R", "methods": "not-a-list"}]},
                {"roles": [{"name": "R", "methods": ["prices:write"]}]},   # not path-shaped
                {"expires_at": "..."}):                                    # no roles at all
        result, _t = _verify("prices", _resp(200, SELLER_INFO_OK), _resp(200, bad))
        assert result.outcome is O.SCHEMA_MISMATCH, bad
        assert result.outcome is not O.MISSING_SCOPE


# ── H. unsupported scopes make no call ──────────────────────────────────────

@pytest.mark.parametrize("scope", ["feedbacks", "content", "advert", "stocks"])
def test_unsupported_scopes_perform_no_http_call(scope):
    """We never execute these on Ozon, so checking a permission for them proves nothing.

    `advert` runs on the Performance API with a SEPARATE credential pair; `feedbacks` and
    `content` raise in ozon_client; no Ozon action uses `stocks`.
    """
    result, t = _verify(scope)                 # nothing scripted: any call would fail
    assert result.outcome is O.VERIFICATION_UNSUPPORTED
    assert t.requests == []


# ── I. end to end: audit, secrets, identity ─────────────────────────────────

async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _ozon_connection(db, *, scope="prices", client_id=CLIENT_ID):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id, marketplace="ozon",
                             identity_status="unverified")
    db.add(acc)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace="ozon", status="connected",
        scopes=[scope], workspace_id=ws.id, marketplace_account_id=acc.id,
        ozon_client_id=client_id,
    )
    db.add(conn)
    cred = ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                         secret_enc=credential_vault.encrypt(API_KEY))
    db.add(cred)
    await db.commit()
    return user, conn, acc, cred


def test_end_to_end_verified_writes_no_identity_and_leaks_no_secret(caplog):
    async def go():
        db = await _orm_session()
        user, conn, acc, _cred = await _ozon_connection(db)
        t = FakeTransport(_resp(200, SELLER_INFO_OK), _resp(200, ROLES_WITH_PRICES))

        with caplog.at_level("DEBUG"):
            _c, credential, result = await runner.verify_credential(
                db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.VERIFIED
        await db.refresh(credential)
        await db.refresh(conn)
        await db.refresh(acc)

        assert credential.verification_status == "verified"
        assert conn.verification_status == "verified"
        assert conn.status == "connected"          # execution gate untouched

        # identity discovery is NOT part of this slice, and Ozon gives us no cabinet id anyway
        assert acc.external_account_id is None
        assert acc.identity_status == "unverified"

        # the seller's INN / legal name / the key itself appear nowhere in the audit
        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt)
            .order_by(ConnectionVerificationAttempt.created_at.desc()))).scalars().first()
        row = str(attempt.__dict__)
        for leak in (API_KEY, SELLER_INFO_OK["company"]["inn"],
                     SELLER_INFO_OK["company"]["legal_name"],
                     SELLER_INFO_OK["company"]["ogrn"]):
            assert leak not in row
        assert attempt.probe_key == "ozon.roles.prices"

        assert API_KEY not in caplog.text
        assert SELLER_INFO_OK["company"]["inn"] not in caplog.text
    asyncio.run(go())


def test_end_to_end_timeout_preserves_a_verified_credential():
    async def go():
        db = await _orm_session()
        user, conn, _acc, cred = await _ozon_connection(db)
        cred.verification_status = "verified"
        cred.verified_at = datetime.utcnow()
        await db.commit()

        t = FakeTransport(ProbeTransportError(ProbeTransportError.TIMEOUT))
        _c, credential, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.TIMEOUT
        await db.refresh(credential)
        assert credential.verification_status == "verified", \
            "a timeout destroyed a proven Ozon credential"
    asyncio.run(go())


def test_end_to_end_missing_client_id_records_an_attempt_without_calling_ozon():
    async def go():
        db = await _orm_session()
        user, conn, _acc, _cred = await _ozon_connection(db, client_id=None)
        t = FakeTransport()      # nothing scripted: any call would fail

        _c, credential, result = await runner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=t)

        assert result.outcome is O.INVALID_CREDENTIALS
        assert t.requests == []
        await db.refresh(credential)
        assert credential.verification_status == "invalid_credentials"

        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.outcome == "invalid_credentials"
        assert attempt.error_category == "credential"
    asyncio.run(go())
