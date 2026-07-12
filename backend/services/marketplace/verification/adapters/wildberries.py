"""Wildberries credential probe. The ONLY production module that knows Wildberries (F1.2b-b).

Everything WB-specific lives here: which host serves which scope, how the token is
presented, what a status code means, what the rate-limit headers are called. The spine
knows none of it, which is what lets Ozon and Yandex arrive as sibling files rather than
as branches inside shared code.

Grounded in the official WB OpenAPI specification, not in guesses:

  * `GET <category-host>/ping` — WB documents it as the check for "validity of the auth
    token" AND "whether the token category matches the service" (`x-readonly-method: true`,
    responses 200/401/429). It mutates nothing. Limit: 3 requests / 30 s per host, which is
    why this adapter never retries.
  * `GET common-api.wildberries.ru/api/v1/seller-info` — accepts a token of ANY category.
    That single property is what makes honest 401 discrimination possible at all.
  * Auth is the raw token in the `Authorization` header — no `Bearer` prefix.

The 401 problem, and why this adapter does not take the easy way out. WB answers every
rejection with 401: invalid, expired, revoked and wrong-category are indistinguishable, and
the only differentiator is a free-text `detail` whose values WB does not enumerate — so
parsing it would be building on sand. Treating a bare 401 as `invalid_credentials` would
therefore tell a seller their perfectly good "Цены и скидки" token is broken merely because
it was checked against the feedbacks host. Instead, a 401 triggers ONE seller-info call:
it accepts any category, so a 200 there proves the token is real and the failure was about
the category (`missing_scope`), while a 401 proves the token itself is dead
(`invalid_credentials`).

And when that second call cannot be made — seller-info is rate-limited (1/min, and 1 per
24 h for a Basic token) or the marketplace is down — this adapter refuses to guess. It
returns a TEMPORARY outcome, which the spine will not persist. Not knowing is a fact we are
allowed to record; accusing a seller of a bad token on a coin flip is not.
"""
from __future__ import annotations

from typing import Optional

from ..taxonomy import VerificationOutcome, VerificationResult
from .base import ProbeContext, ProbeRequest, ProbeResponse

ADAPTER_VERSION = "wb-1"

# WB token categories (official). Our scope -> the category that owns it -> its host.
_PING_HOST_BY_SCOPE: dict[str, str] = {
    "feedbacks": "https://feedbacks-api.wildberries.ru",         # Вопросы и отзывы
    "prices":    "https://discounts-prices-api.wildberries.ru",  # Цены и скидки
    "advert":    "https://advert-api.wildberries.ru",            # Продвижение
    "content":   "https://content-api.wildberries.ru",           # Контент
    "stocks":    "https://marketplace-api.wildberries.ru",       # Маркетплейс
    # `promotions` is absent ON PURPOSE — see _unsupported() below.
}

_PING_PATH = "/ping"
_SELLER_INFO_URL = "https://common-api.wildberries.ru/api/v1/seller-info"

# WB's rate-limit headers. Documented in WB's knowledge base but ABSENT from the OpenAPI
# specs, so they are read defensively: a missing header is normal, not a failure. WB has no
# `Retry-After` at all — `X-Ratelimit-Retry` is its stand-in.
_RETRY_HEADER = "x-ratelimit-retry"


def _auth(secret: str) -> dict[str, str]:
    # Raw token, no "Bearer" — WB's securityScheme is `apiKey` in the Authorization header.
    return {"Authorization": secret}


def _retry_after(resp: ProbeResponse) -> Optional[int]:
    raw = resp.headers.get(_RETRY_HEADER)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None      # a header we cannot parse must never break verification


def _result(outcome: VerificationOutcome, probe_key: str, *,
            http_status: int | None = None,
            retry_after_seconds: int | None = None,
            response_schema_status: str | None = None) -> VerificationResult:
    return VerificationResult(
        outcome=outcome,
        probe_key=probe_key,
        adapter_version=ADAPTER_VERSION,
        http_status=http_status,
        retry_after_seconds=retry_after_seconds,
        response_schema_status=response_schema_status,
    )


class WildberriesProbeAdapter:
    """Read-only. Issues GETs and nothing else."""

    async def verify(self, context: ProbeContext, transport) -> VerificationResult:
        host = _PING_HOST_BY_SCOPE.get(context.scope)
        if host is None:
            return self._unsupported(context.scope)

        probe_key = f"wb.ping.{context.scope}"
        resp = await transport.request(ProbeRequest(
            method="GET", url=f"{host}{_PING_PATH}", headers=_auth(context.secret),
        ))

        if resp.status == 200:
            # A 200 is not enough on its own: WB documents the body as {"TS", "Status"}
            # with Status == "OK". Anything else means we are not talking to what we think
            # we are, and claiming `verified` from an unrecognised body would be a guess.
            status_field = (resp.json or {}).get("Status") if isinstance(resp.json, dict) else None
            if status_field == "OK":
                return _result(VerificationOutcome.VERIFIED, probe_key,
                               http_status=200, response_schema_status="ok")
            return _result(VerificationOutcome.SCHEMA_MISMATCH, probe_key,
                           http_status=200,
                           response_schema_status="unparseable" if resp.json is None
                           else "missing_fields")

        if resp.status == 429:
            return _result(VerificationOutcome.RATE_LIMITED, probe_key,
                           http_status=429, retry_after_seconds=_retry_after(resp))

        if resp.status == 402:
            # Documented by WB for its paid "Каталог решений" services on an empty balance.
            return _result(VerificationOutcome.TARIFF_RESTRICTED, probe_key, http_status=402)

        if resp.status >= 500:
            return _result(VerificationOutcome.MARKETPLACE_UNAVAILABLE, probe_key,
                           http_status=resp.status)

        if resp.status == 401:
            return await self._discriminate_401(context, transport)

        # Any other 4xx: our request, not the seller's token.
        return _result(VerificationOutcome.ADAPTER_ERROR, probe_key, http_status=resp.status)

    async def _discriminate_401(self, context: ProbeContext, transport) -> VerificationResult:
        """A 401 on the category host means "invalid" OR "wrong category" — WB will not say which.

        seller-info accepts a token of ANY category, so it separates the two. It is called
        ONLY here, on the failure path: on a healthy connection it is never touched, which
        keeps its 1/min (1 per 24 h for Basic tokens) budget intact for the case that needs it.
        """
        probe_key = f"wb.seller_info.{context.scope}"
        resp = await transport.request(ProbeRequest(
            method="GET", url=_SELLER_INFO_URL, headers=_auth(context.secret),
        ))

        if resp.status == 200:
            # The token is real — so the /ping 401 was about the CATEGORY, not the token.
            # The body carries the seller's name, INN and `sid`; none of it is read, kept or
            # returned. Identity discovery is a later slice and needs its own decisions.
            return _result(VerificationOutcome.MISSING_SCOPE, probe_key,
                           http_status=200, response_schema_status="ok")

        if resp.status == 401:
            return _result(VerificationOutcome.INVALID_CREDENTIALS, probe_key, http_status=401)

        if resp.status == 429:
            # We could not find out. Saying `invalid_credentials` here would accuse a seller
            # whose token may be perfectly fine — on no evidence at all.
            return _result(VerificationOutcome.RATE_LIMITED, probe_key,
                           http_status=429, retry_after_seconds=_retry_after(resp))

        if resp.status == 402:
            return _result(VerificationOutcome.TARIFF_RESTRICTED, probe_key, http_status=402)

        if resp.status >= 500:
            return _result(VerificationOutcome.MARKETPLACE_UNAVAILABLE, probe_key,
                           http_status=resp.status)

        return _result(VerificationOutcome.ADAPTER_ERROR, probe_key, http_status=resp.status)

    @staticmethod
    def _unsupported(scope: str) -> VerificationResult:
        """`promotions` has no proven WB token category, so it is not probed at all.

        Our `promotions` actions reach `/api/v1/promotions/participation` on the
        discounts-prices host, which suggests the "Цены и скидки" category — but WB does not
        document that mapping, and a probe built on the guess would report a confident
        verdict about a token category we picked ourselves. No network call is made; the
        honest answer is that we cannot check this yet.
        """
        return VerificationResult(
            outcome=VerificationOutcome.VERIFICATION_UNSUPPORTED,
            probe_key=f"wb.unsupported.{scope}",
            adapter_version=ADAPTER_VERSION,
        )
