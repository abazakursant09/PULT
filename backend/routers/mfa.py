"""
MFA router — TOTP-based two-factor authentication.
Uses only Python stdlib: hmac, hashlib, base64, struct, time, secrets.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import BigInteger, String, bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_cookie import set_session_cookie
from database import get_db
from dependencies import get_current_user
from models.mfa_secret import MFASecret
from rate_limit import client_ip
from services.auth_throttle import reserve as _throttle_reserve, release as _throttle_release
from services.mfa_crypto import store_secret, load_secret
from models.user import User

router = APIRouter()


# ── TOTP helpers ──────────────────────────────────────────────────────────────

def _generate_secret() -> str:
    """Generate a random 20-byte base32-encoded secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8")


def _totp(secret: str, ts: int) -> str:
    """Compute a 6-digit TOTP code for the given Unix timestamp."""
    key  = base64.b32decode(secret.upper())
    msg  = struct.pack(">Q", ts // 30)
    h    = hmac.new(key, msg, hashlib.sha1).digest()
    off  = h[-1] & 0x0F
    code = struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def verify_totp_step(secret: str, code: str, now: Optional[int] = None) -> Optional[int]:
    """Return the TOTP *step* (unix_time // 30) that `code` matches within ±1 step (±30 s clock
    skew), or None if it matches none. If the code is valid for more than one step in the window,
    the NEWEST (maximum) matched step is returned, so the replay guard always advances forward.

    A pure function: it performs NO database write and never logs the secret or the code. `now` is
    injectable for deterministic tests. Comparison is constant-time (hmac.compare_digest). A
    malformed / wrong-length code simply matches nothing and yields None.
    """
    candidate = (code or "").strip().zfill(6)
    base = int(time.time()) if now is None else int(now)
    matched: Optional[int] = None
    for delta in (-30, 0, 30):
        ts = base + delta
        if hmac.compare_digest(_totp(secret, ts), candidate):
            step = ts // 30
            if matched is None or step > matched:
                matched = step
    return matched


def verify_totp(secret: str, code: str) -> bool:
    """Thin boolean wrapper over verify_totp_step. NOTE: this only checks a code — it does NOT
    consume the step, so it MUST NOT be used on any auth path. Every runtime verify path
    (login MFA, enable, disable) goes through claim_totp_step, which reserves the step atomically."""
    return verify_totp_step(secret, code) is not None


# ── Atomic replay guard ────────────────────────────────────────────────────────

# One statement reserves the matched step: it succeeds only when the step is strictly newer than the
# last one spent (or none was spent yet), so the SAME code is accepted at most once and two concurrent
# verifies of one code produce exactly one winner (the second re-reads the row after the first commits,
# finds last_totp_step already == step, and matches 0 rows). No SELECT→compare→UPDATE, no in-memory set.
_CLAIM_STEP = text("""
UPDATE mfa_secrets
   SET last_totp_step = :step
 WHERE user_id = :uid
   AND (last_totp_step IS NULL OR last_totp_step < :step)
RETURNING id
""").bindparams(bindparam("step", type_=BigInteger()), bindparam("uid", type_=String()))


async def claim_totp_step(
    db: AsyncSession, *, user_id: str, secret: str, code: str, now: Optional[int] = None
) -> bool:
    """Verify `code` and atomically reserve its TOTP step IN THE CURRENT TRANSACTION.

    Returns True when the code is valid AND its step was newly reserved (accept); False when the code
    is wrong OR its step was already spent (replay / prior step). This does NOT commit — the caller
    commits, so the step-claim commits together with whatever the caller does next (enable/disable in
    one transaction; login commits the claim before issuing the cookie). On a database error it rolls
    back and raises HTTP 503 (fail-closed: the caller must NOT issue a session, and a failed claim is
    reported as a service error, never as a wrong code). Never logs the secret, code, step or user.
    """
    matched_step = verify_totp_step(secret, code, now)
    if matched_step is None:
        return False
    try:
        row = (await db.execute(_CLAIM_STEP, {"step": matched_step, "uid": str(user_id)})).first()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")
    return row is not None


# ── Schemas ───────────────────────────────────────────────────────────────────

class MFAStatusOut(BaseModel):
    enabled: bool


class MFASetupOut(BaseModel):
    secret:  str
    otpauth: str


class MFACodeIn(BaseModel):
    code: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=MFAStatusOut)
async def mfa_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return whether MFA is currently enabled for the user."""
    result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == current_user.id)
    )
    record = result.scalar_one_or_none()
    return MFAStatusOut(enabled=bool(record and record.enabled))


@router.post("/setup", response_model=MFASetupOut)
async def mfa_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a new TOTP secret and persist it (not yet enabled).
    The user must call /verify with a valid code to activate MFA.
    """
    result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == current_user.id)
    )
    record = result.scalar_one_or_none()

    if record and record.enabled:
        raise HTTPException(status_code=400, detail="MFA уже включена. Сначала отключите её.")

    secret = _generate_secret()
    stored = store_secret(secret)

    if record:
        record.secret  = stored
        record.enabled = False
    else:
        record = MFASecret(user_id=current_user.id, secret=stored, enabled=False)
        db.add(record)

    await db.commit()

    issuer  = "Бизнес-Пульт"
    label   = f"{issuer}:{current_user.email}"
    otpauth = (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={issuer}"
        f"&algorithm=SHA1&digits=6&period=30"
    )
    return MFASetupOut(secret=secret, otpauth=otpauth)


# SECURITY-2C-4A — bump token_version and RETURN the written value in the SAME transaction, so the new
# version is captured in a local variable and the cookie can be built AFTER commit with NO further DB
# read. This closes the post-commit gap: a db.refresh() could fail after the bump committed, leaving the
# old cookie revoked and no new one issued.
_BUMP_TOKEN_VERSION = text(
    "UPDATE users SET token_version = token_version + 1 WHERE id = :uid RETURNING token_version"
).bindparams(bindparam("uid", type_=String()))


async def _mfa_manage_throttle_or_429(db: AsyncSession, request: Request, *, user_id: str) -> None:
    """SECURITY-2C-4A — DURABLE per-account throttle for MFA MANAGEMENT (enable + disable). Its own
    action `mfa_manage`, separate from `mfa_login`, so a management attack cannot lock login and vice
    versa. identity is the authenticated user_id (never email). Reserved BEFORE the TOTP claim; 429 +
    Retry-After if blocked. The reservation commits immediately (2C-2 invariant): a DB error inside
    reserve counts nothing (503); a DB error AFTER it leaves the one attempt counted (Option-C)."""
    res = await _throttle_reserve(db, "mfa_manage", identity=user_id, ip=client_ip(request))
    if res.blocked:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Подождите и попробуйте снова.",
            headers={"Retry-After": str(res.retry_after_seconds)},
        )


@router.post("/verify")
async def mfa_verify(
    body: MFACodeIn,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm the TOTP code and enable MFA for the account."""
    result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == current_user.id)
    )
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=400, detail="Сначала запустите настройку MFA (/setup)")
    if record.enabled:
        raise HTTPException(status_code=400, detail="MFA уже активирована")

    await _mfa_manage_throttle_or_429(db, request, user_id=str(current_user.id))

    # Reserve the code's TOTP step, flip `enabled`, and bump token_version in ONE transaction: the
    # step-claim, activation and session revocation commit together (or none do). A replayed / wrong
    # code fails at the claim (400) and changes nothing; a DB error raises 503 with the old session
    # still valid. Enabling MFA is a security state change, so every OTHER device's cookie is revoked.
    if not await claim_totp_step(db, user_id=current_user.id,
                                 secret=load_secret(record.secret), code=body.code):
        raise HTTPException(status_code=400, detail="Неверный код — проверьте приложение аутентификатора")

    from routers.auth import create_access_token   # local import avoids the auth↔mfa import cycle
    record.enabled = True
    # Bump token_version and CAPTURE the written value in-transaction (RETURNING) — the enable flush and
    # the revocation land in ONE commit, and the new version is a plain local, so no post-commit DB read
    # is needed to build the cookie.
    new_version = (await db.execute(_BUMP_TOKEN_VERSION,
                                    {"uid": str(current_user.id)})).scalar_one()
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")

    # Commit succeeded → the old cookie is now revoked. Issue the new cookie IMMEDIATELY from the value
    # captured above. Nothing between the commit and this line touches the DB, so no post-commit failure
    # can leave the account with a dead old cookie and no new one.
    set_session_cookie(response, create_access_token(str(current_user.id), int(new_version)))
    # Best-effort throttle compensation AFTER the cookie is set: a failure here only leaves this one
    # successful attempt counted (benign) and can never affect the session that was just issued.
    try:
        await _throttle_release(db, "mfa_manage", identity=str(current_user.id), ip=client_ip(request))
    except SQLAlchemyError:
        pass
    return {"message": "MFA успешно включена"}


@router.delete("/disable")
async def mfa_disable(
    body: MFACodeIn,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable MFA after confirming with the current TOTP code."""
    result = await db.execute(
        select(MFASecret).where(MFASecret.user_id == current_user.id)
    )
    record = result.scalar_one_or_none()

    if not record or not record.enabled:
        raise HTTPException(status_code=400, detail="MFA не включена")

    await _mfa_manage_throttle_or_429(db, request, user_id=str(current_user.id))

    # Reserve the code's step, disable, and bump token_version in ONE transaction: a replayed / wrong
    # code cannot turn MFA off (400, nothing changes), and a DB error (503) leaves MFA enabled with the
    # old session valid. Turning MFA off is a security downgrade, so every OTHER device's cookie is
    # revoked here too.
    if not await claim_totp_step(db, user_id=current_user.id,
                                 secret=load_secret(record.secret), code=body.code):
        raise HTTPException(status_code=400, detail="Неверный код")

    from routers.auth import create_access_token   # local import avoids the auth↔mfa import cycle
    record.enabled = False
    new_version = (await db.execute(_BUMP_TOKEN_VERSION,
                                    {"uid": str(current_user.id)})).scalar_one()
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")

    # Cookie from the captured version, immediately after commit — no post-commit DB read (see enable).
    set_session_cookie(response, create_access_token(str(current_user.id), int(new_version)))
    try:
        await _throttle_release(db, "mfa_manage", identity=str(current_user.id), ip=client_ip(request))
    except SQLAlchemyError:
        pass
    return {"message": "MFA отключена"}
