"""
Ozon Performance API — OAuth2 token exchange + in-process bearer cache
(Action Coverage Expansion A2.2-pre-b.2).

The Ozon Performance API (advertising / campaign control) authenticates with an
OAuth2 client_credentials grant — distinct from the Seller Api-Key used elsewhere.
This module ONLY adds the token-exchange seam and a TTL cache for the resulting
bearer. It implements NO campaign endpoints, NO executor wiring, NO binding.

Secret safety (doctrine): client_secret and access_token are NEVER logged and
never appear in repr / error messages. Errors are mapped onto the ExecutionError
taxonomy with secret-free detail.

Credential source: the existing ApiCredential row, reused — no new schema:
  scope            = "advert_performance"
  secret_enc       = Ozon Performance client_secret (Fernet)
  meta["client_id"]= Ozon Performance client_id
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.api_credential import ApiCredential
from . import credential_vault
from .errors import ExecutionError

log = logging.getLogger(__name__)

PERFORMANCE_SCOPE = "advert_performance"
_TOKEN_PATH = "/api/client/token"
# refresh slightly before real expiry so an in-flight call never uses a dead token
_REFRESH_MARGIN = timedelta(seconds=60)


@dataclass(frozen=True, repr=False)
class OzonPerformanceToken:
    access_token: str
    expires_in: int
    expires_at: datetime

    def is_fresh(self, *, now: datetime, margin: timedelta = _REFRESH_MARGIN) -> bool:
        return (self.expires_at - now) > margin

    def __repr__(self) -> str:  # never expose the bearer
        return f"OzonPerformanceToken(access_token=***, expires_at={self.expires_at.isoformat()})"


@dataclass(frozen=True)
class PerformanceCredential:
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # never expose the secret
        return f"PerformanceCredential(client_id={self.client_id!r}, client_secret=***)"


async def get_ozon_performance_token(
    *,
    client_id: str,
    client_secret: str,
    base_url: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    now: Optional[datetime] = None,
) -> OzonPerformanceToken:
    """Exchange client_id/client_secret for a Performance bearer (no caching here).

    POST {base}/api/client/token
      {client_id, client_secret, grant_type: "client_credentials"}
    `transport` is injectable for tests (httpx.MockTransport). Maps any non-2xx /
    malformed response to a secret-free ExecutionError(AUTH)."""
    ts = now or datetime.utcnow()
    base = (base_url or settings.ozon_performance_base).rstrip("/")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.marketplace_http_timeout, transport=transport
        ) as client:
            resp = await client.post(f"{base}{_TOKEN_PATH}", json=payload)
    except httpx.HTTPError as exc:
        # transport-level; never echo exc text (may carry url/secret-ish data)
        raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "ozon performance token transport error") from exc

    if resp.status_code >= 400:
        # do NOT include the response body — it may echo credentials
        raise ExecutionError(ExecutionError.AUTH,
                             f"ozon performance token rejected ({resp.status_code})")

    try:
        data = resp.json()
    except ValueError as exc:
        raise ExecutionError(ExecutionError.AUTH, "ozon performance token malformed response") from exc

    access_token = data.get("access_token")
    expires_in = data.get("expires_in")
    if not access_token or not isinstance(expires_in, (int, float)):
        raise ExecutionError(ExecutionError.AUTH, "ozon performance token missing fields")

    expires_in = int(expires_in)
    return OzonPerformanceToken(
        access_token=str(access_token),
        expires_in=expires_in,
        expires_at=ts + timedelta(seconds=expires_in),
    )


class OzonPerformanceTokenCache:
    """In-process, per-key TTL cache for Performance bearers. No persistence.

    Key is caller-chosen (connection_id, else client_id). A cached token is reused
    until it is within the refresh margin of expiry; then it is re-fetched. `now`
    is injectable so tests are deterministic."""

    def __init__(self) -> None:
        self._store: dict[str, OzonPerformanceToken] = {}

    def invalidate(self, key: str) -> None:
        """Drop a key (e.g. after a later 401 from the Performance API)."""
        self._store.pop(key, None)

    def peek(self, key: str) -> Optional[OzonPerformanceToken]:
        return self._store.get(key)

    async def get_token(
        self,
        *,
        key: str,
        fetch: Callable[[], Awaitable[OzonPerformanceToken]],
        now: Optional[datetime] = None,
    ) -> OzonPerformanceToken:
        ts = now or datetime.utcnow()
        cached = self._store.get(key)
        if cached is not None and cached.is_fresh(now=ts):
            return cached
        token = await fetch()
        self._store[key] = token
        return token


# module-level cache instance (in-process only)
_CACHE = OzonPerformanceTokenCache()


def cache() -> OzonPerformanceTokenCache:
    return _CACHE


async def resolve_performance_credential(
    db: AsyncSession, *, connection_id: str
) -> PerformanceCredential:
    """Read the existing ApiCredential (scope=advert_performance) for a connection.

    Returns client_id (meta) + decrypted client_secret. Raises a controlled,
    secret-free ExecutionError(MISSING_SCOPE) when the credential or client_id is
    absent — never a guess, never a partial credential."""
    cred = (await db.execute(select(ApiCredential).where(
        ApiCredential.connection_id == connection_id,
        ApiCredential.scope == PERFORMANCE_SCOPE,
    ))).scalars().first()
    if cred is None:
        raise ExecutionError(ExecutionError.MISSING_SCOPE,
                             f"no '{PERFORMANCE_SCOPE}' credential for connection")
    client_id = (cred.meta or {}).get("client_id")
    if not client_id:
        raise ExecutionError(ExecutionError.MISSING_SCOPE,
                             f"'{PERFORMANCE_SCOPE}' credential missing meta.client_id")
    return PerformanceCredential(
        client_id=str(client_id),
        client_secret=credential_vault.decrypt(cred.secret_enc),
    )
