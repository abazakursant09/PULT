"""
Base HTTP client for marketplace APIs (RFC §3). Centralizes timeout, retry with
backoff on retryable statuses, rate-limit handling, and secret masking. Maps
transport/HTTP failures onto the ExecutionError taxonomy so the executor never
sees a raw httpx error.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings
from .errors import ExecutionError

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BaseMarketplaceClient:
    def __init__(self, base_url: str, *, max_retries: int = 2):
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        auth_header: str = "Authorization",
        extra_headers: dict | None = None,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """
        Perform an authenticated request. Returns parsed JSON (or {} for empty
        bodies). Raises ExecutionError on any non-2xx / transport failure.

        `token` is supplied by the executor from the vault at call time and is
        never logged.
        """
        url = f"{self._base_url}{path}"
        headers = {auth_header: token, "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(
                    timeout=settings.marketplace_http_timeout
                ) as client:
                    resp = await client.request(
                        method, url, headers=headers, json=json, params=params
                    )
            except httpx.TimeoutException as exc:
                if attempt <= self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise ExecutionError(ExecutionError.TIMEOUT, "marketplace timeout") from exc
            except httpx.HTTPError as exc:
                # transport-level; do not include exc text (may carry url/token-ish data)
                raise ExecutionError(
                    ExecutionError.MARKETPLACE_5XX, "transport error"
                ) from exc

            if resp.status_code in _RETRYABLE_STATUS and attempt <= self._max_retries:
                await self._backoff(attempt)
                continue

            return self._handle_response(resp)

    @staticmethod
    async def _backoff(attempt: int) -> None:
        await asyncio.sleep(min(2 ** attempt, 8) * 0.25)

    @staticmethod
    def _handle_response(resp: httpx.Response) -> dict:
        sc = resp.status_code
        if 200 <= sc < 300:
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text[:500]}
        if sc in (401, 403):
            raise ExecutionError(ExecutionError.AUTH, f"auth rejected ({sc})")
        if sc == 429:
            raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
        if 400 <= sc < 500:
            # body may contain useful validation detail (no secrets — it's the response)
            detail = resp.text[:300] if resp.content else ""
            raise ExecutionError(ExecutionError.MARKETPLACE_4XX, f"{sc}: {detail}")
        raise ExecutionError(ExecutionError.MARKETPLACE_5XX, f"{sc}")
