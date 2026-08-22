import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from jose import jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, update, text, bindparam, Boolean, DateTime, String
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from dependencies import get_current_user, get_current_user_optional
from auth_cookie import set_session_cookie, clear_session_cookie
from rate_limit import client_ip
from services.auth_throttle import reserve as _throttle_reserve, release as _throttle_release
from models.mfa_secret import MFASecret
from services.mfa_crypto import load_secret
from models.referral_record import ReferralRecord
from models.user import User
from models.workspace import Workspace
from models.consent_record import ConsentRecord
from routers.mfa import claim_totp_step
from services.email import send_verification_email, send_password_reset_email
from services.reset_token import hash_reset_token
from schemas.auth import (
    ForgotPasswordResponse, RegisterResponse, SessionResponse,
    UserLogin, UserRegister, UserResponse,
)

IP_REG_LIMIT     = 3      # max new accounts per IP per 24 h
IP_REG_WINDOW_H  = 24
_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gen_referral_code() -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(8))

log = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# SECURITY-2C-2 — a fixed bcrypt hash so an UNKNOWN email runs the same password-verify cost as a real
# one (removes the timing oracle). Computed once at import.
_DUMMY_PW_HASH = hash_password("throttle-timing-equalizer-2c2-fixed")


async def _throttle_or_429(db: AsyncSession, action: str, request: Request, *, identity: str = "") -> None:
    """Reserve one attempt for `action` across all its throttle dimensions; 429 (with Retry-After) if the
    caller is blocked. The reservation is committed even if the surrounding request later fails, so a
    failed login is always counted. Identity is hashed (never stored) — unknown and real emails share the
    same bucket space, so the throttle reveals nothing about whether an address exists."""
    res = await _throttle_reserve(db, action, identity=identity, ip=_client_ip(request))
    if res.blocked:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Подождите и попробуйте снова.",
            headers={"Retry-After": str(res.retry_after_seconds)},
        )


def create_access_token(user_id: str, token_version: int) -> str:
    # SECURITY-2C-1 — `ver` is the user's token_version at issue time (server-side, never from client).
    # get_current_user rejects a JWT whose ver != the user's current token_version, so logout / reset /
    # delete (which increment it) revoke every previously issued cookie. Only sub/exp/ver — no jti/iat.
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "exp": expire, "ver": int(token_version)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_mfa_pending_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=5)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "mfa_pending": True},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _client_ip(request: Optional[Request]) -> Optional[str]:
    # Single source of truth for the client IP — the trusted-proxy-aware resolver in
    # rate_limit. The old first-of-X-Forwarded-For read let a client spoof the value and
    # slip the registration IP cap; the shared helper only trusts hops our own proxies
    # appended.
    if request is None:
        return None
    return client_ip(request)


# SECURITY-2C-3C — the per-attempt audit-row writer was removed. That table stored plaintext email + IP
# for every attempt, had ZERO readers (no auth decision, no endpoint, no report — the durable
# brute-force state lives in the 2C-2 throttle buckets as HMAC fingerprints), and only accumulated PII.
# Auth flows no longer persist attempts; security counters remain in the throttle.


# ── MFA login schema ──────────────────────────────────────────────────────────

class MFALoginIn(BaseModel):
    mfa_token: str
    code:      str


# ── Register ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # LEGAL-PRELAUNCH-F2 (blocker #6) — server-side consent is mandatory and checked BEFORE any DB
    # mutation (throttle reserve, user create, recovery). A missing/non-boolean `consent` is already a
    # 422 from the schema (StrictBool); an explicit `false` is refused here. So no user or consent row
    # is ever written without consent, and a direct API call cannot bypass the frontend checkbox.
    if data.consent is not True:
        raise HTTPException(status_code=400, detail="Требуется согласие на обработку персональных данных")

    ip = _client_ip(request)
    # SECURITY-2C-2 — request-rate throttle (per email + per IP) on top of the existing 24h creation cap;
    # catches rapid existing-email probing that the creation cap (counts only CREATED users) would miss.
    await _throttle_or_429(db, "register", request, identity=data.email)

    # ── IP rate limit (account CREATION cap, 24h) ─────────────────────────────
    if ip:
        cutoff = datetime.utcnow() - timedelta(hours=IP_REG_WINDOW_H)
        ip_cnt = await db.execute(
            select(func.count(User.id)).where(
                User.registered_ip == ip,
                User.created_at >= cutoff,
            )
        )
        if (ip_cnt.scalar_one() or 0) >= IP_REG_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"С этого IP уже создано {IP_REG_LIMIT} аккаунта за последние 24 часа. Попробуйте позже.",
            )

    # ── Check for existing user (including soft-deleted) ──────────────────────
    result = await db.execute(select(User).where(User.email == data.email))
    existing = result.scalar_one_or_none()

    if existing and not existing.deleted_at:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")

    # ── Account recovery ──────────────────────────────────────────────────────
    if existing and existing.deleted_at:
        verification_token = secrets.token_urlsafe(32)
        existing.hashed_password       = hash_password(data.password)
        existing.name                  = data.name
        existing.deleted_at            = None
        existing.is_restored           = True
        existing.verification_token    = verification_token
        existing.is_verified           = False
        existing.registered_ip         = ip
        # Preserve referral_code, referred_by_id, was_referrer, was_referred
        # Do NOT create a new ReferralRecord — bonuses don't repeat
        # LEGAL-PRELAUNCH-F2 — a recovery is a fresh consent ACTION: append ONE new evidence row (never
        # overwrite the user's prior rows) in the SAME transaction as the restore, so a failure rolls
        # back both. Server UTC time + server document version; nothing client-supplied.
        db.add(ConsentRecord(
            user_id=existing.id,
            consent_at=datetime.utcnow(),
            consent_version=settings.consent_doc_version,
            context="recovery",
        ))
        await db.commit()
        await db.refresh(existing)
        log.info("account_restored: user=%s", existing.id)   # no email/IP in application logs
        await send_verification_email(existing.email, existing.name, verification_token)
        return RegisterResponse(
            message="Аккаунт восстановлен. Проверьте почту и подтвердите email для входа.",
        )

    # ── New registration ──────────────────────────────────────────────────────
    verification_token = secrets.token_urlsafe(32)
    user = User(
        email=data.email,
        name=data.name,
        hashed_password=hash_password(data.password),
        is_verified=False,
        verification_token=verification_token,
        registered_ip=ip,
    )

    # Resolve referral code
    referrer = None
    if data.ref_code:
        ref_result = await db.execute(
            select(User).where(
                User.referral_code == data.ref_code.upper(),
                User.deleted_at    == None,         # noqa: E711
            )
        )
        referrer = ref_result.scalar_one_or_none()
        if referrer and referrer.email != data.email:
            user.referred_by_id = referrer.id

    db.add(user)
    await db.flush()   # populate user.id before creating the record

    # Ownership boundary (F1.0): every user owns exactly one workspace, created in the
    # same transaction so a failed registration cannot leave an orphan. The recovery
    # branch above reuses the existing user row, and therefore its existing workspace.
    db.add(Workspace(owner_user_id=user.id))

    if referrer and user.referred_by_id:
        from models.referral_record import ReferralRecord
        db.add(ReferralRecord(referrer_id=referrer.id, invitee_id=user.id))

    # LEGAL-PRELAUNCH-F2 — exactly ONE append-only consent evidence row, in the SAME transaction as the
    # user + workspace: server UTC time + server document-set version, never client-supplied. A failure
    # here rolls the whole registration back — no user without evidence, no evidence without a user.
    db.add(ConsentRecord(
        user_id=user.id,
        consent_at=datetime.utcnow(),
        consent_version=settings.consent_doc_version,
        context="registration",
    ))

    await db.commit()
    await db.refresh(user)

    log.info("register: user=%s ref=%s", user.id, data.ref_code)   # no email/IP in application logs
    # The account is kept whatever happens to the mail — losing it would be worse, and the seller
    # can resend. But the RESULT is no longer discarded: telling someone to check an inbox we
    # failed to write to is the lie that left them stranded.
    sent = await send_verification_email(user.email, user.name, verification_token)
    if not sent:
        # No token, no address beyond the id — the operator can find the account, the log cannot
        # be used to verify anyone.
        log.warning("register: verification email not delivered user=%s", user.id)

    return RegisterResponse(
        message=("Аккаунт создан. Проверьте почту и подтвердите email для входа."
                 if sent else
                 "Аккаунт создан, но письмо отправить не удалось."),
        verification_email_sent=sent,
    )


# ── Verify email ──────────────────────────────────────────────────────────────

@router.get("/verify-email", response_model=SessionResponse)
async def verify_email(
    response: Response,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.verification_token == token)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Недействительная или устаревшая ссылка подтверждения")

    if not user.is_verified:
        user.is_verified = True
        user.verification_token = None
        await db.commit()
        await db.refresh(user)
        log.info("email_verified: user=%s", user.id)   # no email in application logs

    set_session_cookie(response, create_access_token(str(user.id), user.token_version))
    return SessionResponse(user=UserResponse.model_validate(user))


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    # SECURITY-2C-2 — reserve BEFORE the bcrypt check (identity+IP / identity / IP dimensions); 429 if
    # blocked. Same throttle path for known and unknown emails → no existence oracle.
    await _throttle_or_429(db, "login", request, identity=data.email)

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Unknown email: run the SAME bcrypt cost against a fixed dummy hash so the response time does not
    # reveal whether the address exists, then fail identically.
    if user is None:
        verify_password(data.password, _DUMMY_PW_HASH)
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    # Password is correct → this was NOT a brute-force failure: compensate this request's own +1 on
    # every login dimension (only real failures net-accumulate; the IP's prior failures still stand).
    await _throttle_release(db, "login", identity=data.email, ip=ip)

    if user.deleted_at:
        raise HTTPException(status_code=403, detail="Аккаунт удалён. Зарегистрируйтесь повторно для восстановления.")

    if not user.is_verified:
        raise HTTPException(
            status_code=403,
            detail="Подтвердите email перед входом. Проверьте почту или используйте ссылку из страницы регистрации.",
        )

    mfa_result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == user.id)
    )
    mfa_record = mfa_result.scalar_one_or_none()

    if mfa_record and mfa_record.enabled:
        mfa_token = create_mfa_pending_token(str(user.id))
        return {"mfa_required": True, "mfa_token": mfa_token}

    log.info("login_success: user=%s", user.id)   # no email/IP in application logs
    set_session_cookie(response, create_access_token(str(user.id), user.token_version))
    return SessionResponse(user=UserResponse.model_validate(user))


# ── MFA verify step ───────────────────────────────────────────────────────────

@router.post("/login/mfa", response_model=SessionResponse)
async def login_mfa(
    data: MFALoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    from jose import JWTError

    try:
        payload = jwt.decode(
            data.mfa_token, settings.secret_key, algorithms=[settings.algorithm]
        )
        user_id = payload.get("sub")
        is_pending = payload.get("mfa_pending", False)
        if not user_id or not is_pending:
            raise HTTPException(status_code=401, detail="Недействительный MFA-токен")
    except JWTError:
        raise HTTPException(status_code=401, detail="Недействительный или просроченный MFA-токен")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    mfa_result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == user_id)
    )
    mfa_record = mfa_result.scalar_one_or_none()

    if not mfa_record or not mfa_record.enabled:
        raise HTTPException(status_code=400, detail="MFA не настроена")

    # SECURITY-2C-4A — DURABLE per-account TOTP-guess throttle (action mfa_login, dims pair/identity/ip;
    # identity is the server-validated pre-MFA user_id, never email). Reserved BEFORE the crypto check;
    # 429 + Retry-After if blocked. Replaces the old in-memory limiter, so the cap survives restarts and
    # is shared across workers. The reservation commits immediately (2C-2 invariant): a failure INSIDE
    # reserve counts nothing (upsert atomic, 503); a failure AFTER it (the claim/commit below) leaves the
    # one reserved attempt counted — fail-closed, honest Option-C semantics.
    await _throttle_or_429(db, "mfa_login", request, identity=str(user_id))

    # SECURITY-2C-3A — verify AND atomically consume the code's TOTP step. A wrong code or a
    # replayed / prior step returns False → 401 (counted as a failed attempt). A DB error inside the
    # claim raises 503 (fail-closed: no cookie), never a false "wrong code". The reserved step is
    # committed BEFORE the session cookie is issued, so one code authenticates at most once even under
    # two concurrent /login/mfa requests (the second re-reads last_totp_step and matches 0 rows).
    if not await claim_totp_step(db, user_id=str(user_id),
                                 secret=load_secret(mfa_record.secret), code=data.code):
        raise HTTPException(status_code=401, detail="Неверный код аутентификатора")

    try:
        await db.commit()   # burn the reserved step before any session is issued
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")

    # Correct code → NOT a brute-force failure: compensate this request's own +1 on every mfa_login
    # dimension (only real guesses net-accumulate; an already-set block is never cleared).
    await _throttle_release(db, "mfa_login", identity=str(user_id), ip=_client_ip(request))

    log.info("mfa_verified: user=%s", user_id)   # no IP in application logs
    set_session_cookie(response, create_access_token(str(user.id), user.token_version))
    return SessionResponse(user=UserResponse.model_validate(user))


# ── Current session ───────────────────────────────────────────────────────────
@router.get("/me", response_model=UserResponse)
async def me(response: Response, user: User = Depends(get_current_user)):
    """The current user, resolved from the HttpOnly session cookie. 401 when the cookie is absent /
    invalid / expired. Lets the frontend hydrate auth state on reload without ever holding a token."""
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


# ── Logout ──────────────────────────────────────────────────────────────────────
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """SECURITY-2C-1 — REAL server-side revocation. Atomically bump token_version so EVERY previously
    issued JWT for this user (this device and any other) is rejected by get_current_user; a copied
    cookie stops working immediately. The bump is a single atomic SQL increment (`token_version + 1`),
    NOT a Python read-modify-write, so two concurrent logouts never lose an update and never 500; the
    counter only ever increases. Commit lands BEFORE the cookie is cleared. Idempotent: a missing /
    invalid / already-revoked cookie resolves user=None → we just clear the cookie and return 204.
    Still covered by the central Origin-CSRF check."""
    if user is not None:
        await db.execute(
            update(User).where(User.id == user.id).values(token_version=User.token_version + 1)
        )
        await db.commit()
    clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ── Resend verification ───────────────────────────────────────────────────────

class ResendVerificationIn(BaseModel):
    email: EmailStr


class ResendVerificationResponse(BaseModel):
    # ONE field, and it is a constant. This endpoint is unauthenticated and takes an arbitrary
    # address, so ANY field that varies with the state of that address is an enumeration oracle.
    #
    # A delivery flag used to live here. It read `false` only for an existing-and-unverified
    # address whose send was refused, and `true` for everything else — which means that during an
    # SMTP outage the response told an anonymous caller which addresses are registered. Honesty
    # about delivery belongs on the registration response, where the caller demonstrably owns the
    # account they just created; here it can only describe someone else's.
    message: str


# Deliberately in the future tense and conditional. It promises nothing about an address the
# caller may not own, and reads identically whether a letter left, failed, or was never attempted.
_NEUTRAL_RESEND = "Если адрес зарегистрирован и требует подтверждения, письмо будет отправлено."


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(
    data: ResendVerificationIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Re-send the email-verification link.

    Registration swallows a mail delivery failure and returns 201, and the verification token is
    delivered only by email — so a seller whose first mail never arrives had no way back in and no
    resend path. This closes that dead-end. It mints a FRESH token (invalidating the old one, in
    line with the single-token design) and re-sends it. The token is never returned. Rate-limited
    like the other unauthenticated auth endpoints.

    The response is a CONSTANT — same status, same fields, same text, same types — for an unknown
    address, an already-verified one, a deleted one, a successful send and a refused send alike.
    Nothing observable to the caller distinguishes those five cases, so the endpoint cannot be used
    to test whether an address is registered. A real SMTP failure is recorded server-side instead.
    """
    # SECURITY-2C-2 — per-email + per-IP throttle (replaces the in-memory per-IP limiter); the neutral
    # response and this shared throttle keep it enumeration-safe.
    await _throttle_or_429(db, "email", request, identity=data.email)
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Only an existing, non-deleted, still-unverified account gets a mail. Everything else falls
    # through to the same neutral message with no observable difference.
    if user and not user.deleted_at and not user.is_verified:
        token = secrets.token_urlsafe(32)
        user.verification_token = token
        await db.commit()
        log.info("resend_verification: user=%s", user.id)
        if not await send_verification_email(user.email, user.name, token):
            # The operator's only channel for this failure. Account id only: no token, no
            # password, no message body — and nothing about it reaches the caller.
            log.warning("resend_verification: email not delivered user=%s", user.id)

    return ResendVerificationResponse(message=_NEUTRAL_RESEND)


# ── Forgot password ───────────────────────────────────────────────────────────

class ForgotPasswordIn(BaseModel):
    email: EmailStr


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    data: ForgotPasswordIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # SECURITY-2C-2 — per-email + per-IP throttle (replaces the in-memory per-IP limiter). The neutral
    # response below plus this shared throttle keep it enumeration-safe.
    await _throttle_or_429(db, "email", request, identity=data.email)
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user:
        # Don't reveal whether email is registered
        return ForgotPasswordResponse(
            message="Если этот email зарегистрирован, мы отправили на него ссылку для сброса пароля.",
        )

    # SECURITY-2C-3B — the raw token (256-bit) goes ONLY into the emailed link; the DB stores only its
    # SHA-256 digest, so a DB read yields no usable reset link.
    raw_token = secrets.token_urlsafe(32)
    user.reset_token = hash_reset_token(raw_token)
    user.reset_token_expires = datetime.utcnow() + timedelta(hours=24)
    await db.commit()

    # user id only — never the email (PII) or the token — so the log can never become an enumeration
    # or account-takeover aid.
    log.info("password_reset_requested: user=%s", user.id)
    # The RESPONSE stays byte-identical whether the address exists or the send failed — this is the
    # one endpoint where an honest delivery flag would hand an attacker an account-enumeration
    # oracle. The failure goes to the server log instead, carrying the user id and no token, so an
    # operator can see the outage without the log becoming a way to verify anybody.
    if not await send_password_reset_email(user.email, user.name, raw_token):
        log.warning("password_reset: email not delivered user=%s", user.id)

    return ForgotPasswordResponse(
        message="Если этот email зарегистрирован, мы отправили на него ссылку для сброса пароля.",
    )


# ── Reset password ────────────────────────────────────────────────────────────

class ResetPasswordIn(BaseModel):
    token:    str
    password: str


# SECURITY-2C-3B — ONE atomic statement is the whole reset: match the token DIGEST (never a plaintext
# lookup), check it is unexpired, set the new password, CONSUME the token (NULL), revoke every prior
# session (token_version+1), and verify the account — all committed together or not at all. Two
# concurrent confirms of one token can therefore succeed only once: the first NULLs reset_token, so the
# second's `WHERE reset_token = :digest` matches 0 rows and changes nothing. No SELECT→mutate→commit.
# Booleans are typed binds (never an int literal) so the UPDATE runs identically on PostgreSQL and
# SQLite; `now` is a typed naive-UTC bind to match the timestamp column on both drivers.
_RESET_CONSUME = text("""
UPDATE users
   SET hashed_password     = :hash,
       reset_token         = NULL,
       reset_token_expires = NULL,
       token_version       = token_version + 1,
       is_verified         = :verified,
       verification_token  = NULL
 WHERE reset_token = :digest
   AND reset_token_expires IS NOT NULL
   AND reset_token_expires > :now
RETURNING id
""").bindparams(
    bindparam("hash", type_=String()), bindparam("digest", type_=String()),
    bindparam("now", type_=DateTime()), bindparam("verified", type_=Boolean()),
)


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # SECURITY-2C-2 — per-IP throttle on the reset-confirm (identity is unknown until the token resolves;
    # the reset TOKEN is never used as a throttle key and never logged).
    await _throttle_or_429(db, "reset", request)

    # Trivial input errors first — this never touches (or burns) a token, and a short-password 422 is
    # identical whether the token is valid or not, so it leaks nothing.
    if len(data.password) < 8:
        raise HTTPException(status_code=422, detail="Пароль — минимум 8 символов")

    # Hash the digest and the new password BEFORE the DB write: only the digest is ever bound into SQL
    # (a leaked SQL error can expose the digest, never the raw token), and an unknown token runs the
    # same bcrypt cost as a real one.
    digest = hash_reset_token(data.token)
    new_hash = hash_password(data.password)

    try:
        row = (await db.execute(_RESET_CONSUME, {
            "hash": new_hash, "digest": digest, "verified": True,
            "now": datetime.utcnow(),
        })).first()
        if row is None:
            # Unknown / expired / already-used all look identical from outside (neutral 400).
            await db.rollback()
            raise HTTPException(status_code=400, detail="Недействительная или устаревшая ссылка")
        await db.commit()
    except SQLAlchemyError:
        # Fail-closed: nothing is committed, the token stays valid, and we never surface SQL/params.
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")

    # The Core UPDATE bypassed the ORM identity map; expire anything this session already loaded so a
    # later read in the same session reflects the write (production opens a fresh session per request).
    db.expire_all()
    log.info("password_reset_completed: user=%s", row.id)
    return {"message": "Пароль успешно изменён. Войдите с новым паролем."}
