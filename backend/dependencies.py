from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import User

# auto_error=False so missing Authorization header doesn't 403 —
# we fall back to the pult_token cookie instead.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Priority: cookie > Authorization header
    token: Optional[str] = None
    cookie_val = request.cookies.get("pult_token")
    if cookie_val:
        token = cookie_val
    elif credentials:
        token = credentials.credentials

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
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None instead of raising 401.
    Used by fire-and-forget endpoints that accept anonymous events."""
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None
