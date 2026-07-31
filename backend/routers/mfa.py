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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import BigInteger, String, bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.mfa_secret import MFASecret
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


@router.post("/verify")
async def mfa_verify(
    body: MFACodeIn,
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
    # Reserve the code's TOTP step and flip `enabled` in ONE transaction: the step-claim and the
    # activation commit together (or neither does). A replayed code fails at the claim (400); a DB
    # error raises 503 without enabling. The code spent to enable cannot be reused for an immediate
    # login — the user waits for the next 30-second code (documented UX cost of one code).
    if not await claim_totp_step(db, user_id=current_user.id,
                                 secret=load_secret(record.secret), code=body.code):
        raise HTTPException(status_code=400, detail="Неверный код — проверьте приложение аутентификатора")

    record.enabled = True
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")
    return {"message": "MFA успешно включена"}


@router.delete("/disable")
async def mfa_disable(
    body: MFACodeIn,
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
    # Reserve the code's step and disable in ONE transaction: a replayed code cannot turn MFA off,
    # and a DB error (503) leaves MFA enabled rather than half-disabled.
    if not await claim_totp_step(db, user_id=current_user.id,
                                 secret=load_secret(record.secret), code=body.code):
        raise HTTPException(status_code=400, detail="Неверный код")

    record.enabled = False
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Сервис временно недоступен. Повторите попытку.")
    return {"message": "MFA отключена"}
