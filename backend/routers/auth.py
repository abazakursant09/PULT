import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from rate_limit import limit_auth
from models.login_attempt import LoginAttempt
from models.mfa_secret import MFASecret
from models.referral_record import ReferralRecord
from models.user import User
from routers.mfa import verify_totp
from schemas.auth import (
    ForgotPasswordResponse, RegisterResponse, TokenResponse,
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


def create_access_token(user_id: str, extra: Optional[dict] = None) -> str:
    expire  = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_mfa_pending_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=5)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "mfa_pending": True},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def _log(
    db: AsyncSession,
    *,
    email: str,
    success: bool,
    action: str,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    attempt = LoginAttempt(
        email=email,
        ip_address=ip,
        success=success,
        action=action,
        reason=reason,
    )
    db.add(attempt)
    await db.commit()


# ── MFA login schema ──────────────────────────────────────────────────────────

class MFALoginIn(BaseModel):
    mfa_token: str
    code:      str


class MFALoginOut(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         UserResponse


# ── Register ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)

    # ── IP rate limit ─────────────────────────────────────────────────────────
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
        await _log(db, email=data.email, success=False, action="register",
                   reason="Email уже зарегистрирован", ip=ip)
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
        await db.commit()
        await db.refresh(existing)
        log.info("account_restored: user=%s email=%s ip=%s", existing.id, data.email, ip)
        await _log(db, email=data.email, success=True, action="register_restore", ip=ip)
        return RegisterResponse(
            message="Аккаунт восстановлен. Подтвердите email для входа.",
            verification_token=verification_token,
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

    if referrer and user.referred_by_id:
        from models.referral_record import ReferralRecord
        db.add(ReferralRecord(referrer_id=referrer.id, invitee_id=user.id))

    await db.commit()
    await db.refresh(user)

    log.info("register: user=%s email=%s ip=%s ref=%s", user.id, data.email, ip, data.ref_code)
    await _log(db, email=data.email, success=True, action="register", ip=ip)

    return RegisterResponse(
        message="Аккаунт создан. Подтвердите email для входа.",
        verification_token=verification_token,
    )


# ── Verify email ──────────────────────────────────────────────────────────────

@router.get("/verify-email", response_model=TokenResponse)
async def verify_email(
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
        log.info("email_verified: user=%s email=%s", user.id, user.email)

    access_token = create_access_token(str(user.id))
    return TokenResponse(access_token=access_token, user=UserResponse.model_validate(user))


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(limit_auth),
):
    ip = _client_ip(request)

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        await _log(db, email=data.email, success=False, action="login",
                   reason="Неверный email или пароль", ip=ip)
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    if user.deleted_at:
        await _log(db, email=data.email, success=False, action="login",
                   reason="Аккаунт удалён", ip=ip)
        raise HTTPException(status_code=403, detail="Аккаунт удалён. Зарегистрируйтесь повторно для восстановления.")

    if not user.is_verified:
        await _log(db, email=data.email, success=False, action="login",
                   reason="Email не подтверждён", ip=ip)
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
        await _log(db, email=data.email, success=True, action="login",
                   reason="mfa_required", ip=ip)
        return {"mfa_required": True, "mfa_token": mfa_token}

    log.info("login_success: user=%s email=%s ip=%s", user.id, data.email, ip)
    await _log(db, email=data.email, success=True, action="login", ip=ip)
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


# ── MFA verify step ───────────────────────────────────────────────────────────

@router.post("/login/mfa", response_model=MFALoginOut)
async def login_mfa(
    data: MFALoginIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from jose import JWTError

    ip = _client_ip(request)

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

    if not verify_totp(mfa_record.secret, data.code):
        await _log(db, email=user.email, success=False, action="mfa_verify",
                   reason="Неверный TOTP-код", ip=ip)
        raise HTTPException(status_code=401, detail="Неверный код аутентификатора")

    log.info("mfa_verified: user=%s ip=%s", user_id, ip)
    await _log(db, email=user.email, success=True, action="mfa_verify", ip=ip)
    token = create_access_token(str(user.id))
    return MFALoginOut(access_token=token, user=UserResponse.model_validate(user))


# ── Forgot password ───────────────────────────────────────────────────────────

class ForgotPasswordIn(BaseModel):
    email: EmailStr


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    data: ForgotPasswordIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(limit_auth),
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user:
        # Don't reveal whether email is registered
        return ForgotPasswordResponse(
            message="Если этот email зарегистрирован, ссылка будет показана.",
            reset_token=None,
        )

    reset_token = secrets.token_urlsafe(32)
    user.reset_token = reset_token
    user.reset_token_expires = datetime.utcnow() + timedelta(hours=24)
    await db.commit()

    log.info("password_reset_requested: user=%s email=%s", user.id, data.email)

    return ForgotPasswordResponse(
        message="Ссылка для сброса пароля сгенерирована.",
        reset_token=reset_token,
    )


# ── Reset password ────────────────────────────────────────────────────────────

class ResetPasswordIn(BaseModel):
    token:    str
    password: str


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordIn,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.reset_token == data.token)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Недействительная или устаревшая ссылка")

    if user.reset_token_expires and datetime.utcnow() > user.reset_token_expires:
        raise HTTPException(status_code=400, detail="Ссылка сброса пароля истекла. Запросите новую.")

    if len(data.password) < 8:
        raise HTTPException(status_code=422, detail="Пароль — минимум 8 символов")

    user.hashed_password = hash_password(data.password)
    user.reset_token = None
    user.reset_token_expires = None
    await db.commit()

    log.info("password_reset_completed: user=%s", user.id)
    return {"message": "Пароль успешно изменён. Войдите с новым паролем."}
