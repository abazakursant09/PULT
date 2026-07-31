from typing import Optional

from fastapi import Depends, HTTPException, Request
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from auth_cookie import read_session_cookie
from models.user import User


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    # SECURITY-2B-2 — the browser session lives ONLY in the HttpOnly cookie. The Authorization: Bearer
    # fallback was removed so a token stolen via XSS/localStorage can no longer authenticate; internal
    # cron/HMAC endpoints keep their own X-Internal-Key auth (they never used this dependency).
    token: Optional[str] = read_session_cookie(request)

    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id: Optional[str] = payload.get("sub")
        # MFA-pending tokens must not access protected endpoints
        if not user_id or payload.get("mfa_pending"):
            raise HTTPException(status_code=401, detail="Недействительный токен")
    except JWTError:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    if getattr(user, "deleted_at", None) is not None:
        raise HTTPException(status_code=403, detail="Аккаунт удалён")
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None instead of raising 401.
    Used by fire-and-forget endpoints that accept anonymous events."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None
