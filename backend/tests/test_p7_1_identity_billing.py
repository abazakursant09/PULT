"""
P7.1 — Identity & Billing Security.

Proves the launch-blocking holes are closed:
- register / forgot-password no longer return auth tokens in the response; the link
  is delivered via the email service instead (verify + reset still work off the token
  persisted in the DB).
- the OAuth stub is disabled (fail-closed 403) and unmounted from the app.
- the free self-upgrade endpoint POST /api/chat/set-plan is gone.
- the YooKassa webhook does NOT change a plan on an unverified (forged) event — it
  activates only when the status is re-confirmed with YooKassa.
"""
import asyncio
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.user import User
from models.payment import Payment

import routers.auth as auth
import routers.payments as payments
import routers.chat as chat
from schemas.auth import UserRegister, RegisterResponse, ForgotPasswordResponse
from routers.auth import (
    register, forgot_password, verify_email, reset_password,
    ForgotPasswordIn, ResetPasswordIn,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _Req:
    headers: dict = {}

    class _C:
        host = "1.2.3.4"
    client = _C()


def _patch_email(monkeypatch):
    sent: list[tuple] = []

    async def _rec_verify(to, name, token):
        sent.append(("verify", to, token)); return True

    async def _rec_reset(to, name, token):
        sent.append(("reset", to, token)); return True

    monkeypatch.setattr(auth, "send_verification_email", _rec_verify)
    monkeypatch.setattr(auth, "send_password_reset_email", _rec_reset)
    return sent


# ── 1. register: no token in response, email sent ────────────────────────────

def test_register_no_token_sends_email(monkeypatch):
    sent = _patch_email(monkeypatch)

    async def go():
        db = await _engine()
        resp = await register(UserRegister(email="a@b.com", name="Ann", password="Passw0rd"),
                              _Req(), db)
        assert isinstance(resp, RegisterResponse)
        dumped = resp.model_dump()
        # Deliberately an EXACT set, not a "no token" substring check: anything that appears in
        # this response must be justified here, one field at a time. `verification_email_sent` is
        # a boolean DELIVERY fact (B6) — it says whether SMTP accepted the letter and nothing at
        # all about the link, which is still delivered only by email.
        assert set(dumped.keys()) == {"message", "verification_email_sent"}
        assert "verification_token" not in dumped
        assert isinstance(dumped["verification_email_sent"], bool)
        assert sent and sent[0][0] == "verify" and sent[0][1] == "a@b.com"
        # token persisted in DB (delivered by email, not response)
        user = (await db.execute(select(User).where(User.email == "a@b.com"))).scalar_one()
        assert user.verification_token and user.is_verified is False
    _run(go())


# ── 2. verify-email still works off the persisted token ──────────────────────

def test_verify_email_flow(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _engine()
        await register(UserRegister(email="v@b.com", name="V", password="Passw0rd"), _Req(), db)
        user = (await db.execute(select(User).where(User.email == "v@b.com"))).scalar_one()
        tok = user.verification_token
        resp = await verify_email(tok, db)
        assert resp.access_token
        refreshed = (await db.execute(select(User).where(User.email == "v@b.com"))).scalar_one()
        assert refreshed.is_verified is True
    _run(go())


# ── 3. forgot-password: no token in response, email sent, token in DB ────────

def test_forgot_password_no_token_sends_email(monkeypatch):
    sent = _patch_email(monkeypatch)

    async def go():
        db = await _engine()
        await register(UserRegister(email="f@b.com", name="F", password="Passw0rd"), _Req(), db)
        sent.clear()
        resp = await forgot_password(ForgotPasswordIn(email="f@b.com"), _Req(), db, _rl=None)
        assert isinstance(resp, ForgotPasswordResponse)
        dumped = resp.model_dump()
        assert set(dumped.keys()) == {"message"}          # NO reset_token
        assert "reset_token" not in dumped
        assert sent and sent[0][0] == "reset" and sent[0][1] == "f@b.com"
        user = (await db.execute(select(User).where(User.email == "f@b.com"))).scalar_one()
        assert user.reset_token                            # persisted, delivered by email
    _run(go())


# ── 4. reset-password still works off the persisted token ────────────────────

def test_reset_password_flow(monkeypatch):
    sent = _patch_email(monkeypatch)

    async def go():
        db = await _engine()
        await register(UserRegister(email="r@b.com", name="R", password="Passw0rd"), _Req(), db)
        await forgot_password(ForgotPasswordIn(email="r@b.com"), _Req(), db, _rl=None)
        tok = sent[-1][2]
        resp = await reset_password(ResetPasswordIn(token=tok, password="NewPass0rd"), db)
        assert "message" in resp
        user = (await db.execute(select(User).where(User.email == "r@b.com"))).scalar_one()
        assert auth.verify_password("NewPass0rd", user.hashed_password)
        assert user.reset_token is None
    _run(go())


# ── 5. OAuth disabled: handler fail-closed 403 + route unmounted ─────────────

def test_oauth_disabled():
    from routers.oauth import oauth_login, OAuthLoginIn
    with pytest.raises(HTTPException) as ei:
        _run(oauth_login(OAuthLoginIn(provider="google", provider_user_id="x", email="a@b.com")))
    assert ei.value.status_code == 403
    import main
    assert "/api/auth/oauth/login" not in set(main.app.openapi()["paths"])


# ── 6. free self-upgrade endpoint removed ────────────────────────────────────

def test_set_plan_endpoint_removed():
    assert not hasattr(chat, "set_plan")
    import main
    assert "/api/chat/set-plan" not in set(main.app.openapi()["paths"])


# ── 7. webhook does not activate on unverified event; activates on verified ──

def _seed_pending_payment(db):
    uid = str(uuid.uuid4())
    user = User(id=uid, email=f"{uid}@b.com", name="P",
                hashed_password=auth.hash_password("Passw0rd"))
    db.add(user)
    pay = Payment(user_id=uid, yookassa_payment_id="yk_1", amount=Decimal("4990.00"),
                  tariff="pro", plan="profi", status="pending")
    db.add(pay)
    return uid


class _JReq:
    async def json(self):
        return {"event": "payment.succeeded", "object": {"id": "yk_1"}}


def test_webhook_ignores_forged_event(monkeypatch):
    async def go():
        db = await _engine(); uid = _seed_pending_payment(db); await db.commit()

        async def _unverified(yk_id):
            return None                                   # YooKassa not confirming
        monkeypatch.setattr(payments, "_yk_fetch_status", _unverified)

        await payments.yookassa_webhook(_JReq(), db)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        pay = (await db.execute(select(Payment).where(Payment.yookassa_payment_id == "yk_1"))).scalar_one()
        assert pay.status == "pending"                    # NOT flipped by the forged body
        assert user.plan == "master"                      # plan NOT granted
    _run(go())


def test_webhook_activates_on_verified(monkeypatch):
    async def go():
        db = await _engine(); uid = _seed_pending_payment(db); await db.commit()

        async def _verified(yk_id):
            return "succeeded"                            # YooKassa confirms
        monkeypatch.setattr(payments, "_yk_fetch_status", _verified)

        await payments.yookassa_webhook(_JReq(), db)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        pay = (await db.execute(select(Payment).where(Payment.yookassa_payment_id == "yk_1"))).scalar_one()
        assert pay.status == "succeeded"
        assert user.plan == "profi"                       # granted only after verification
    _run(go())
