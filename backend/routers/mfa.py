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
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.mfa_secret import MFASecret
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


def verify_totp(secret: str, code: str) -> bool:
    """Verify TOTP within ±1 time-step (±30 s) to account for clock skew."""
    now = int(time.time())
    for delta in (-30, 0, 30):
        expected = _totp(secret, now + delta)
        if hmac.compare_digest(expected, code.strip().zfill(6)):
            return True
    return False


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

    if record:
        record.secret  = secret
        record.enabled = False
    else:
        record = MFASecret(user_id=current_user.id, secret=secret, enabled=False)
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
    if not verify_totp(record.secret, body.code):
        raise HTTPException(status_code=400, detail="Неверный код — проверьте приложение аутентификатора")

    record.enabled = True
    await db.commit()
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
    if not verify_totp(record.secret, body.code):
        raise HTTPException(status_code=400, detail="Неверный код")

    record.enabled = False
    await db.commit()
    return {"message": "MFA отключена"}
