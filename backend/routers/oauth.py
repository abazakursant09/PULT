"""
OAuth stub router.
In production each provider would verify an id_token / access_token against
their own API. Here we accept the validated payload directly so the frontend
can simulate the flow without real client_id / secrets.
"""
import secrets
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.oauth_account import OAuthAccount
from routers.auth import create_access_token
from schemas.auth import UserResponse, TokenResponse

log = logging.getLogger(__name__)
router = APIRouter()

PROVIDERS = {"google", "apple", "yandex"}


class OAuthLoginIn(BaseModel):
    provider:         str
    provider_user_id: str
    email:            str | None = None
    name:             str | None = None


@router.post("/oauth/login", response_model=TokenResponse)
async def oauth_login(
    data: OAuthLoginIn,
    db: AsyncSession = Depends(get_db),
):
    if data.provider not in PROVIDERS:
        raise HTTPException(400, f"Неизвестный провайдер: {data.provider}")

    # 1. Find existing OAuth account → get linked user
    q = await db.execute(
        select(OAuthAccount)
        .where(OAuthAccount.provider         == data.provider)
        .where(OAuthAccount.provider_user_id == data.provider_user_id)
    )
    oauth = q.scalar_one_or_none()

    if oauth:
        user_q = await db.execute(select(User).where(User.id == oauth.user_id))
        user = user_q.scalar_one_or_none()
        if not user:
            raise HTTPException(404, "Пользователь не найден")
        log.info("oauth_login: existing link provider=%s user=%s", data.provider, user.id)
    else:
        # 2. Try to link to existing account by email
        user = None
        if data.email:
            user_q = await db.execute(select(User).where(User.email == data.email))
            user = user_q.scalar_one_or_none()

        # 3. Create new user if none found
        if not user:
            if not data.email:
                raise HTTPException(400, "Email обязателен для первого входа через OAuth")
            name = (data.name or data.email.split("@")[0]).strip() or "User"
            user = User(
                email=data.email,
                name=name,
                hashed_password=secrets.token_hex(32),
                is_verified=True,
            )
            db.add(user)
            await db.flush()
            log.info("oauth_login: new user created provider=%s email=%s", data.provider, data.email)
        else:
            log.info("oauth_login: linked to existing user provider=%s user=%s", data.provider, user.id)

        # 4. Record the OAuth link
        oauth = OAuthAccount(
            user_id=str(user.id),
            provider=data.provider,
            provider_user_id=data.provider_user_id,
            email=data.email,
            name=data.name,
        )
        db.add(oauth)

    await db.commit()
    await db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))
