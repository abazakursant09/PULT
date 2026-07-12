"""Yandex Market credential probe. The ONLY production module that knows Yandex (F1.2d).

Grounded in the official Partner API documentation:

  * Auth is a static `Api-Key` header — a bare token, no `Bearer`, no scheme prefix. OAuth
    is explicitly deprecated ("we do not recommend using an OAuth token, as this
    authorization method is outdated"), so there is no OAuth flow here and never will be.
    An Api-Key is minted by the seller in their cabinet and does not expire.
  * `POST /v2/auth/token` returns the ACCESS GROUPS the presented token holds. The docs
    state it plainly: "Список доступов по переданному Api-Key-токену можно получить с
    помощью метода POST v2/auth/token".

That introspection endpoint is what makes per-scope verification possible with the scope
vocabulary we already have — it is Yandex's answer to Ozon's `POST /v1/roles`. Without it
we could only prove that a token is alive (via `GET /campaigns`), which would have forced
either a new scope literal or a false claim that a token grants `prices` when nobody
checked. Neither was necessary.

Two things Yandex does differently, and both are handled here rather than in the spine:

  * Throttling is **420 Enhance Your Calm**, not 429. A client that only special-cases 429
    silently mishandles it. The spine never classifies statuses — it hands them to the
    adapter — so this needed no change anywhere else.
  * There is no `Retry-After`. The budget is described by `X-RateLimit-Resource-*` headers,
    with the reset expressed as an RFC-822 *timestamp*, not a duration.

Error bodies carry `errors[].message`, and it is never parsed: it is prose, not a contract.
401 and 403 are cleanly separated by the documentation — 401 means the credentials are
dead, 403 means they are alive but under-permissioned — and that split is all we need.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from ..taxonomy import VerificationOutcome, VerificationResult
from .base import ProbeContext, ProbeRequest, ProbeResponse

ADAPTER_VERSION = "yandex-1"

_TOKEN_INFO_URL = "https://api.partner.market.yandex.ru/v2/auth/token"

# Yandex grants Api-Key tokens ACCESS GROUPS (not OAuth scopes). `all-methods` and
# `all-methods:read-only` subsume every group, so they satisfy any scope we ask about.
_ALL_METHODS = ("all-methods", "all-methods:read-only")

# Our scope -> the Yandex access groups that satisfy it. Read-only variants are accepted
# because PULT performs no writes on Yandex at all: there is no Yandex client in the
# executor, so a read-only token is exactly what this connection is for.
#
# `feedbacks` and `advert` are absent ON PURPOSE — see _unsupported() below.
_SCOPE_GROUPS: dict[str, tuple[str, ...]] = {
    "prices": ("pricing", "pricing:read-only") + _ALL_METHODS,
    "content": ("offers-and-cards-management",
                "offers-and-cards-management:read-only") + _ALL_METHODS,
    "stocks": ("inventory-and-order-processing",
               "inventory-and-order-processing:read-only") + _ALL_METHODS,
    "promotions": ("promotion", "promotion:read-only") + _ALL_METHODS,
}

_LIMIT_HEADER = "x-ratelimit-resource-limit"
_UNTIL_HEADER = "x-ratelimit-resource-until"
_REMAINING_HEADER = "x-ratelimit-resource-remaining"


def _headers(secret: str) -> dict[str, str]:
    # Bare token. No "Bearer": Yandex authenticates with the Api-Key header itself.
    return {"Api-Key": secret}


def _retry_after(resp: ProbeResponse, *, now: Optional[datetime] = None) -> Optional[int]:
    """Seconds until the budget resets, from an RFC-822 timestamp.

    Yandex sends no `Retry-After` — only a moment in time. Everything here is defensive:
    an absent, unparseable or already-past header yields None, never an exception and never
    a negative wait. A malformed header must not be able to break a verification.
    """
    raw = resp.headers.get(_UNTIL_HEADER)
    if not raw:
        return None
    try:
        until = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    seconds = int((until - now).total_seconds())
    return seconds if seconds > 0 else None


def _result(outcome: VerificationOutcome, probe_key: str, *,
            http_status: int | None = None,
            retry_after_seconds: int | None = None,
            response_schema_status: str | None = None) -> VerificationResult:
    return VerificationResult(
        outcome=outcome, probe_key=probe_key, adapter_version=ADAPTER_VERSION,
        http_status=http_status, retry_after_seconds=retry_after_seconds,
        response_schema_status=response_schema_status,
    )


def _access_groups(body: Any) -> Optional[list[str]]:
    """The token's access groups, or None when the payload is not what we expect.

    The exact field name is not pinned by a public schema, so several documented shapes are
    accepted. What is NOT done is guessing: an unrecognised body returns None and the caller
    reports a schema mismatch. Inventing a verdict from a payload we cannot read would be
    the one unforgivable move — a wrong `missing_scope` accuses a seller whose token is fine.
    """
    if not isinstance(body, dict):
        return None

    container = body.get("result") if isinstance(body.get("result"), dict) else body
    for key in ("authScopes", "auth_scopes", "scopes", "accesses", "access"):
        value = container.get(key)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return [v.strip().lower() for v in value if v.strip()]

    return None


class YandexProbeAdapter:
    """Read-only. One call, no writes, no retries."""

    async def verify(self, context: ProbeContext, transport) -> VerificationResult:
        groups_for_scope = _SCOPE_GROUPS.get(context.scope)
        if groups_for_scope is None:
            return self._unsupported(context.scope)

        probe_key = f"yandex.auth_token.{context.scope}"
        resp = await transport.request(ProbeRequest(
            method="POST", url=_TOKEN_INFO_URL, headers=_headers(context.secret), json={},
        ))

        if resp.status == 401:
            # Documented: the token is absent or invalid. Yandex does not distinguish
            # invalid / expired / revoked by code, and the message is prose — so neither do we.
            return _result(VerificationOutcome.INVALID_CREDENTIALS, probe_key, http_status=401)

        if resp.status == 403:
            # Documented: the credentials are ALIVE but lack the rights. Not a bad token.
            return _result(VerificationOutcome.MISSING_SCOPE, probe_key, http_status=403)

        if resp.status == 420:
            # 420 Enhance Your Calm — Yandex throttles with this, not 429.
            return _result(VerificationOutcome.RATE_LIMITED, probe_key, http_status=420,
                           retry_after_seconds=_retry_after(resp))

        if resp.status >= 500:
            return _result(VerificationOutcome.MARKETPLACE_UNAVAILABLE, probe_key,
                           http_status=resp.status)

        if resp.status != 200:
            # Some other 4xx: our request, not the seller's token.
            return _result(VerificationOutcome.ADAPTER_ERROR, probe_key,
                           http_status=resp.status)

        groups = _access_groups(resp.json)
        if groups is None:
            return _result(VerificationOutcome.SCHEMA_MISMATCH, probe_key, http_status=200,
                           response_schema_status="unparseable" if resp.json is None
                           else "missing_fields")

        if any(g in groups_for_scope for g in groups):
            return _result(VerificationOutcome.VERIFIED, probe_key, http_status=200,
                           response_schema_status="ok")

        # The token is valid — it simply was not minted with the group this scope needs.
        return _result(VerificationOutcome.MISSING_SCOPE, probe_key, http_status=200,
                       response_schema_status="ok")

    @staticmethod
    def _unsupported(scope: str) -> VerificationResult:
        """`feedbacks` and `advert` have no proven Yandex access group, so nothing is called.

        Yandex has no "advert" group at all — promotion is its closest neighbour, and
        assuming they are the same is a guess. Which group owns goods-feedback is likewise
        unconfirmed. Reporting a confident verdict about a mapping we chose ourselves is
        exactly the failure this whole contour exists to prevent, so we say we cannot check.
        """
        return VerificationResult(
            outcome=VerificationOutcome.VERIFICATION_UNSUPPORTED,
            probe_key=f"yandex.unsupported.{scope}",
            adapter_version=ADAPTER_VERSION,
        )
