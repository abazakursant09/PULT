"""When the mail server fails, the seller must not be left holding an account they cannot enter.

The dead end this closes: `send_email` swallows every exception and returns False, and every caller
in auth.py threw that value away. Registration therefore answered 201 with a cheerful "Проверьте
почту" whether the mail was delivered, merely logged, or refused outright. Login is hard-blocked
until verification, resend went down the same broken path, and `reset_password` never set
`is_verified` — so password recovery could not rescue them either. The only exits were an operator
reading the log or editing the database by hand.

Two changes, both narrow:

  * Delivery results are no longer discarded. Registration and resend report whether the letter
    actually left, so the screen can stop claiming a letter is coming when none is.
  * Completing a password reset marks the account verified. Using that link PROVES control of the
    mailbox — it arrived by email, it is checked for validity and expiry, and it is consumed. That
    is exactly the evidence email verification exists to obtain.

What must NOT change, and is pinned below: no token is ever returned or logged, forgot-password
stays byte-identical whether or not the address exists, unverified users still cannot log in, and
merely ASKING for a mail verifies nobody.
"""
import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from rate_limit import limit_auth
import models  # noqa: F401  registers tables
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_auth] = lambda: None    # the limiter itself is tested elsewhere
    return TestClient(app)


GOOD_PW = "Passw0rdOk"


def _register(client, email=None):
    return client.post("/api/auth/register", json={
        "email": email or f"{uuid.uuid4()}@example.com", "name": "S", "password": GOOD_PW,
    })


async def _seed(db, *, verified=False, reset_token=None, expires=None, email=None):
    addr = email or f"{uuid.uuid4()}@example.com"
    user = User(id=str(uuid.uuid4()), email=addr, name="S",
                hashed_password=hash_password(GOOD_PW), is_verified=verified,
                verification_token=None if verified else "verify-me",
                reset_token=reset_token, reset_token_expires=expires)
    db.add(user)
    await db.commit()
    return user


def _sent(ok: bool):
    """Patch the mail layer at the auth-router boundary, leaving the SMTP transport untouched."""
    async def _fake(*a, **k):
        return ok
    return _fake


# ── 1-3. Registration tells the truth about the letter ──────────────────────

def test_registration_with_working_mail_says_check_your_inbox():
    db = _run(_new_db())
    with patch.object(auth_router, "send_verification_email", _sent(True)):
        r = _register(_client(db))
    assert r.status_code in (200, 201)
    body = r.json()
    assert body["verification_email_sent"] is True
    assert "Проверьте почту" in body["message"]


def test_a_failed_send_still_creates_the_account():
    """Losing the account would be worse than a missing letter — they can resend."""
    db = _run(_new_db())
    with patch.object(auth_router, "send_verification_email", _sent(False)):
        r = _register(_client(db), email="stranded@example.com")
    assert r.status_code in (200, 201)
    assert r.json()["verification_email_sent"] is False
    user = _run(db.execute(select(User).where(User.email == "stranded@example.com"))).scalars().first()
    assert user is not None and user.is_verified is False


def test_a_failed_send_never_claims_the_mail_was_sent():
    """THE defect: the old response said "Проверьте почту" for a letter that did not exist."""
    db = _run(_new_db())
    with patch.object(auth_router, "send_verification_email", _sent(False)):
        body = _register(_client(db)).json()
    assert "Проверьте почту" not in body["message"]
    assert body["verification_email_sent"] is False


# ── 4-5. Resending ──────────────────────────────────────────────────────────

def test_resend_reports_success_once_the_mail_server_recovers():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    with patch.object(auth_router, "send_verification_email", _sent(True)):
        r = _client(db).post("/api/auth/resend-verification", json={"email": user.email})
    assert r.status_code == 200
    assert r.json()["email_sent"] is True


def test_resend_reports_a_repeat_failure_honestly():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    with patch.object(auth_router, "send_verification_email", _sent(False)):
        r = _client(db).post("/api/auth/resend-verification", json={"email": user.email})
    assert r.json()["email_sent"] is False


def test_resend_to_an_unknown_address_stays_neutral():
    """`email_sent` must not become an enumeration oracle in the ordinary case: nothing to send is
    reported the same as a successful send, and the message text never varies."""
    db = _run(_new_db())
    with patch.object(auth_router, "send_verification_email", _sent(True)):
        known = _client(db).post("/api/auth/resend-verification",
                                 json={"email": f"{uuid.uuid4()}@example.com"})
    assert known.json()["email_sent"] is True
    assert "Если аккаунт" in known.json()["message"]


def test_resend_never_returns_a_token():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    with patch.object(auth_router, "send_verification_email", _sent(True)):
        r = _client(db).post("/api/auth/resend-verification", json={"email": user.email})
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.verification_token not in r.text
    assert "token" not in r.json()


# ── 6. The verification link still works ────────────────────────────────────

def test_the_verification_token_verifies():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    r = _client(db).get("/api/auth/verify-email?token=verify-me")
    assert r.status_code == 200
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.is_verified is True


# ── 7-10. Password reset as the second honest route in ──────────────────────

def test_a_valid_reset_verifies_the_account():
    """Using the link proves the mailbox is theirs — this is what rescues a stranded seller."""
    db = _run(_new_db())
    user = _run(_seed(db, verified=False, reset_token="rt-1",
                      expires=datetime.utcnow() + timedelta(hours=1)))
    r = _client(db).post("/api/auth/reset-password",
                         json={"token": "rt-1", "password": GOOD_PW})
    assert r.status_code == 200
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.is_verified is True
    assert fresh.verification_token is None      # the outstanding link is retired with it


def test_an_unknown_reset_token_verifies_nobody():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False, reset_token="rt-1",
                      expires=datetime.utcnow() + timedelta(hours=1)))
    r = _client(db).post("/api/auth/reset-password",
                         json={"token": "not-the-token", "password": GOOD_PW})
    assert r.status_code == 400
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.is_verified is False


def test_an_expired_reset_token_verifies_nobody():
    """An expired link is not evidence of anything — it may have been read by anyone since."""
    db = _run(_new_db())
    user = _run(_seed(db, verified=False, reset_token="rt-old",
                      expires=datetime.utcnow() - timedelta(minutes=1)))
    r = _client(db).post("/api/auth/reset-password",
                         json={"token": "rt-old", "password": GOOD_PW})
    assert r.status_code == 400
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.is_verified is False


def test_a_reset_token_cannot_be_used_twice():
    db = _run(_new_db())
    user = _run(_seed(db, verified=False, reset_token="rt-1",
                      expires=datetime.utcnow() + timedelta(hours=1)))
    client = _client(db)
    first = client.post("/api/auth/reset-password", json={"token": "rt-1", "password": GOOD_PW})
    second = client.post("/api/auth/reset-password", json={"token": "rt-1", "password": "Other123x"})
    assert first.status_code == 200
    assert second.status_code == 400
    del user


def test_a_reset_cannot_verify_someone_elses_address():
    """The token identifies the account. Resetting mine must never touch yours."""
    db = _run(_new_db())
    mine = _run(_seed(db, verified=False, reset_token="rt-mine",
                      expires=datetime.utcnow() + timedelta(hours=1)))
    theirs = _run(_seed(db, verified=False))
    _client(db).post("/api/auth/reset-password", json={"token": "rt-mine", "password": GOOD_PW})
    other = _run(db.execute(select(User).where(User.id == theirs.id))).scalars().first()
    assert other.is_verified is False
    del mine


def test_the_password_rules_are_unchanged():
    db = _run(_new_db())
    _run(_seed(db, verified=False, reset_token="rt-1",
               expires=datetime.utcnow() + timedelta(hours=1)))
    r = _client(db).post("/api/auth/reset-password", json={"token": "rt-1", "password": "short"})
    assert r.status_code == 422


# ── 11-13. Nothing leaks ────────────────────────────────────────────────────

def test_forgot_password_reveals_nothing_about_the_address():
    """The one endpoint where an honest delivery flag would be an enumeration oracle. Its answer
    must be identical for a known address whose mail FAILED and an unknown one."""
    db = _run(_new_db())
    user = _run(_seed(db, verified=True))
    client = _client(db)
    with patch.object(auth_router, "send_password_reset_email", _sent(False)):
        known = client.post("/api/auth/forgot-password", json={"email": user.email})
    unknown = client.post("/api/auth/forgot-password",
                          json={"email": f"{uuid.uuid4()}@example.com"})
    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()
    assert "email_sent" not in known.json()      # no flag to compare at all


def test_no_endpoint_returns_a_verification_or_reset_token():
    db = _run(_new_db())
    client = _client(db)
    with patch.object(auth_router, "send_verification_email", _sent(False)):
        reg = _register(client, email="tokencheck@example.com")
    user = _run(db.execute(
        select(User).where(User.email == "tokencheck@example.com"))).scalars().first()
    assert user.verification_token and user.verification_token not in reg.text

    with patch.object(auth_router, "send_password_reset_email", _sent(True)):
        forgot = client.post("/api/auth/forgot-password", json={"email": user.email})
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.reset_token and fresh.reset_token not in forgot.text


def test_a_delivery_failure_is_logged_without_the_token(caplog):
    import logging
    db = _run(_new_db())
    lg = logging.getLogger("routers.auth")
    prev_disabled, prev_level = lg.disabled, lg.level
    lg.disabled = False
    lg.setLevel(logging.WARNING)
    try:
        with caplog.at_level(logging.WARNING, logger="routers.auth"):
            with patch.object(auth_router, "send_verification_email", _sent(False)):
                _register(_client(db), email="logged@example.com")
    finally:
        lg.disabled, lg.level = prev_disabled, prev_level

    user = _run(db.execute(
        select(User).where(User.email == "logged@example.com"))).scalars().first()
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert user.verification_token not in blob       # the token never reaches a log line


# ── 14-16. The guarantees that must survive ─────────────────────────────────

def test_an_unverified_user_still_cannot_log_in():
    """The whole point of verification. Nothing here may loosen it."""
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    r = _client(db).post("/api/auth/login", json={"email": user.email, "password": GOOD_PW})
    assert r.status_code == 403
    assert "Подтвердите email" in r.json()["detail"]


def test_asking_for_a_mail_verifies_nobody():
    """Only a USED valid token verifies — never the attempt to send one."""
    db = _run(_new_db())
    user = _run(_seed(db, verified=False))
    client = _client(db)
    with patch.object(auth_router, "send_verification_email", _sent(True)):
        client.post("/api/auth/resend-verification", json={"email": user.email})
    with patch.object(auth_router, "send_password_reset_email", _sent(True)):
        client.post("/api/auth/forgot-password", json={"email": user.email})
    fresh = _run(db.execute(select(User).where(User.id == user.id))).scalars().first()
    assert fresh.is_verified is False


def test_the_rate_limiter_is_still_wired_to_these_routes():
    """No endpoint quietly lost its throttle while being touched.

    Registration does not use `limit_auth`; it has its own per-IP account cap. The two endpoints
    that send mail on demand — and would otherwise be a free mail cannon — do use it.
    """
    import inspect
    src = inspect.getsource(auth_router)
    for fn in ("async def resend_verification", "async def forgot_password"):
        start = src.index(fn)
        assert "limit_auth" in src[start:start + 600], f"{fn} lost its rate limit"

    reg = src.index("async def register")
    assert "IP_REG_LIMIT" in src[reg:reg + 2000], "registration lost its per-IP account cap"
