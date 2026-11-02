"""
Base HTTP client for marketplace APIs (RFC §3). Centralizes timeout, retry with
backoff on retryable statuses, rate-limit handling, and secret masking. Maps
transport/HTTP failures onto the ExecutionError taxonomy so the executor never
sees a raw httpx error.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from config import settings
from .errors import ExecutionError

log = logging.getLogger(__name__)

# 420 "Enhance Your Calm" is how Yandex Market signals throttling — it does NOT use 429. Without it
# here a throttled response would fall through to the generic 4xx branch and surface as
# MARKETPLACE_4XX, which the review-sync loop treats as a PRODUCT-local error: the cursor would
# advance and the batch would keep calling a marketplace that just asked us to stop. Both marketplaces
# that use 429 are unaffected — this only adds a second status to the same, existing path.
_RETRYABLE_STATUS = {420, 429, 500, 502, 503, 504}

# Statuses that mean "you are being rate limited", whatever number the marketplace picked for it.
_RATE_LIMIT_STATUS = {420, 429}

# Support IMF-fixdate and asctime only. Obsolete RFC850 dates fail closed: the
# email parser's fixed two-digit-year pivot is not HTTP's relative-century rule.
_HTTP_DATE = re.compile(
    r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [0-9]{4} "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} GMT|"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?: [0-9]|[0-9]{2}) "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4})"
)


def _throttle_hint(headers: httpx.Headers) -> float | None:
    """Maximum supported lower bound; invalid/duplicate hints fail closed."""
    delays = []
    for name in ("Retry-After", "X-Ratelimit-Retry"):
        values = headers.get_list(name)
        if not values:
            continue
        if len(values) != 1:
            raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
        value = values[0].strip(" \t")
        if re.fullmatch(r"[0-9]+", value):
            digits = value.lstrip("0") or "0"
            # Supported invocation budgets never exceed 1.5 seconds. Avoid huge int parsing.
            if len(digits) > 1 or digits > "1":
                raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
            delay = float(digits)
        elif name == "Retry-After" and _HTTP_DATE.fullmatch(value):
            try:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is None:  # RFC asctime form is implicitly GMT.
                    deadline = deadline.replace(tzinfo=timezone.utc)
                delay = max(0, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
            except (ValueError, TypeError, OverflowError):
                raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited") from None
        else:
            raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
        delays.append(delay)
    return max(delays) if delays else None


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
        retry_safe: bool = False,
    ) -> dict:
        """
        Perform an authenticated request. Returns parsed JSON (or {} for empty
        bodies). Raises ExecutionError on any non-2xx / transport failure.

        `token` is supplied by the executor from the vault at call time and is
        never logged.

        Only an explicitly classified repeat-safe operation may replay after an
        ambiguous timeout/5xx. HTTP verbs do not establish safety: some reads use
        POST and some marketplace mutations use GET. Unknown operations fail
        closed for ambiguous replay; existing throttle-refusal retries remain.
        """
        url = f"{self._base_url}{path}"
        headers = {auth_header: token, "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        attempt = 0
        throttle_wait = 0.0
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
                if retry_safe is True and attempt <= self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise ExecutionError(ExecutionError.TIMEOUT, "marketplace timeout") from exc
            except httpx.HTTPError as exc:
                # transport-level; do not include exc text (may carry url/token-ish data)
                raise ExecutionError(
                    ExecutionError.MARKETPLACE_5XX, "transport error"
                ) from exc

            if resp.status_code in _RATE_LIMIT_STATUS:
                # Explicit supported throttle domain; no constructor validation or claim for n>2.
                n = self._max_retries
                if type(n) is not int or not 0 <= n <= 2 or attempt > n:
                    return self._handle_response(resp)
                budget = (0.0, 0.5, 1.5)[n]
                hint = _throttle_hint(resp.headers)
                fallback = 0.5 if attempt == 1 else 1.0
                delay = max(fallback, hint) if hint is not None else fallback
                if delay > budget - throttle_wait:
                    return self._handle_response(resp)
                throttle_wait += delay  # Requested waits only; scheduler oversleep is not charged.
                if hint is None:
                    await self._backoff(attempt)
                else:
                    not_before = monotonic() + delay
                    await asyncio.sleep(delay)
                    if monotonic() < not_before:
                        return self._handle_response(resp)
                continue

            if (
                resp.status_code in _RETRYABLE_STATUS
                and attempt <= self._max_retries
                and retry_safe is True
            ):
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
        if sc in _RATE_LIMIT_STATUS:
            raise ExecutionError(ExecutionError.RATE_LIMIT, "rate limited")
        if 400 <= sc < 500:
            # body may contain useful validation detail (no secrets — it's the response)
            detail = resp.text[:300] if resp.content else ""
            raise ExecutionError(ExecutionError.MARKETPLACE_4XX, f"{sc}: {detail}")
        raise ExecutionError(ExecutionError.MARKETPLACE_5XX, f"{sc}")
