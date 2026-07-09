"""
OAuth — DISABLED (P7.1).

The previous implementation was a STUB that accepted a client-supplied provider_user_id
and email with NO verification against any provider, allowing anyone to obtain a session
for any registered email (account takeover). It is disabled and NOT mounted in main.py.

The handler is retained only as a fail-closed 403 so that, if ever re-mounted by mistake,
it cannot issue a session. A real OAuth flow (server-side id_token/access_token validation
against each provider) is deferred to a later phase.
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()


class OAuthLoginIn(BaseModel):
    provider:         str
    provider_user_id: str
    email:            str | None = None
    name:             str | None = None


@router.post("/oauth/login")
async def oauth_login(data: OAuthLoginIn):
    # Fail closed — OAuth is disabled until a verified provider flow ships.
    raise HTTPException(status_code=403, detail="Вход через OAuth временно недоступен")
