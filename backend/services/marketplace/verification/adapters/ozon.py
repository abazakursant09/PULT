"""Ozon credential probe. The ONLY production module that knows Ozon (F1.2b-c).

Grounded in Ozon's official Seller API specification, verified against the spec itself:

  * `POST /v1/seller/info` (`SellerAPI_SellerInfo`) — no requestBody is declared, and it
    only reads. POST is not a mutation here: Ozon uses POST for read operations, and what
    a method does is decided by the method, not the verb.
  * `POST /v1/roles` (`AccessAPI_RolesByToken`) — returns `expires_at` and
    `roles[].methods[]`, i.e. exactly what this key is permitted to call.
  * Auth is a PAIR of headers: `Api-Key` and `Client-Id`, both declared required.

Ozon is the mirror image of Wildberries here, and in a useful way. WB answers every
rejection with an indistinguishable 401, so scope had to be inferred. Ozon instead states
its permissions outright — which is why `missing_scope` is *observed* here rather than
deduced, and why `revoked` is a real verdict rather than something folded into "invalid":
Ozon documents `Api-key is deactivated` separately from `Invalid Api-Key`.

What Ozon does NOT give us, and where this adapter therefore stays silent:

  * The HTTP status of an auth failure is not documented — `/v1/seller/info` declares only
    `200` and `default`. So classification keys off the documented `message` text with the
    status as a weak signal, never off a hard-coded 401.
  * A wrong `Client-Id` is not distinguishable from a wrong `Api-Key`: Ozon documents no
    error for the former at all. Both collapse into `invalid_credentials`.
  * `Api-Key is restricted to specific IP addresses` means the key is FINE and our egress
    address is not allowed. That is neither an invalid credential nor a missing role, and
    no existing outcome states it honestly — so it is reported as `adapter_error`, which is
    temporary and accuses the seller of nothing. Incomplete, deliberately, rather than wrong.
"""
from __future__ import annotations

from typing import Any, Optional

from ..taxonomy import VerificationOutcome, VerificationResult
from .base import ProbeContext, ProbeRequest, ProbeResponse

ADAPTER_VERSION = "ozon-1"

_SELLER_BASE = "https://api-seller.ozon.ru"
_SELLER_INFO_PATH = "/v1/seller/info"
_ROLES_PATH = "/v1/roles"

# Our scope -> the Ozon method the executor ACTUALLY calls for it (ozon_client.py). The
# mapping is read off our own execution paths, not invented: verifying a scope means asking
# whether the key may call the thing we would really call.
#
# Everything else is absent on purpose. `feedbacks` and `content` raise in ozon_client
# (premium Reviews / unimplemented), `advert` runs on the Performance API with a SEPARATE
# credential pair, and no Ozon action in the catalog uses `stocks`. Probing them would mean
# checking a permission for a call we never make.
_SCOPE_METHOD: dict[str, str] = {
    "prices":     "/v1/product/import/prices",
    "promotions": "/v1/actions/products/activate",
}

# Documented error messages (Ozon Seller API, "Errors"). Matched case-insensitively on a
# stable prefix — never on the whole string, which Ozon may reword.
_MSG_INVALID = "invalid api-key"
_MSG_DEACTIVATED = "api-key is deactivated"
_MSG_MISSING_ROLE = "missing a required role"
_MSG_IP_RESTRICTED = "restricted to specific ip"
_MSG_RATE_LIMIT = "request rate limit"

# Documented for Ozon's item-operation methods only — absent on these two, so it is read
# defensively: no header is normal, not an error. Ozon has no standard `Retry-After`.
_RETRY_HEADER = "item-retry-after"


def _headers(ctx: ProbeContext) -> dict[str, str]:
    return {"Api-Key": ctx.secret, "Client-Id": str(ctx.ozon_client_id)}


def _message(resp: ProbeResponse) -> str:
    body = resp.json
    if isinstance(body, dict):
        msg = body.get("message")
        if isinstance(msg, str):
            return msg.lower()
    return ""


def _retry_after(resp: ProbeResponse) -> Optional[int]:
    raw = resp.headers.get(_RETRY_HEADER)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None       # an unparseable header must never break verification


def _result(outcome: VerificationOutcome, probe_key: str, *,
            http_status: int | None = None,
            retry_after_seconds: int | None = None,
            response_schema_status: str | None = None) -> VerificationResult:
    return VerificationResult(
        outcome=outcome, probe_key=probe_key, adapter_version=ADAPTER_VERSION,
        http_status=http_status, retry_after_seconds=retry_after_seconds,
        response_schema_status=response_schema_status,
    )


def _classify_error(resp: ProbeResponse, probe_key: str) -> VerificationResult:
    """Non-2xx from Ozon. Keyed off the documented message, not off a guessed status."""
    msg = _message(resp)

    if _MSG_DEACTIVATED in msg:
        # Ozon says this outright — so unlike WB, `revoked` is a verdict we can actually make.
        return _result(VerificationOutcome.REVOKED, probe_key, http_status=resp.status)

    if _MSG_INVALID in msg:
        return _result(VerificationOutcome.INVALID_CREDENTIALS, probe_key,
                       http_status=resp.status)

    if _MSG_MISSING_ROLE in msg:
        return _result(VerificationOutcome.MISSING_SCOPE, probe_key, http_status=resp.status)

    if _MSG_IP_RESTRICTED in msg:
        # The key works; WE are calling from an address it does not allow. Blaming the
        # seller's credential here would be a lie, and no existing outcome says this.
        return _result(VerificationOutcome.ADAPTER_ERROR, probe_key, http_status=resp.status)

    if resp.status == 429 or _MSG_RATE_LIMIT in msg:
        return _result(VerificationOutcome.RATE_LIMITED, probe_key,
                       http_status=resp.status, retry_after_seconds=_retry_after(resp))

    if resp.status >= 500:
        return _result(VerificationOutcome.MARKETPLACE_UNAVAILABLE, probe_key,
                       http_status=resp.status)

    # A 4xx we do not recognise. We do not know what it means, so we say exactly that.
    return _result(VerificationOutcome.ADAPTER_ERROR, probe_key, http_status=resp.status)


def _role_covers(roles: Any, method: str) -> Optional[bool]:
    """Does any granted role permit `method`? None when the payload is not what we expect.

    Ozon's example shows entries like `{"name": "Admin", "methods": ["/v1/actions"]}` — a
    prefix. Matching by prefix is correct under BOTH readings: if Ozon returns the full path
    instead, an exact value is still a prefix of itself. If the entries are not paths at all
    we return None rather than guess, and the caller reports a schema mismatch — a wrong
    `missing_scope` would accuse a seller whose key is perfectly fine.
    """
    if not isinstance(roles, list):
        return None

    seen_path = False
    for role in roles:
        if not isinstance(role, dict):
            return None
        methods = role.get("methods")
        if not isinstance(methods, list):
            return None
        for entry in methods:
            if not isinstance(entry, str) or not entry.startswith("/"):
                return None          # not path-shaped: we cannot interpret this
            seen_path = True
            if method.startswith(entry):
                return True

    return False if seen_path else None


class OzonProbeAdapter:
    """Read-only. Two calls, no writes, no retries."""

    async def verify(self, context: ProbeContext, transport) -> VerificationResult:
        method = _SCOPE_METHOD.get(context.scope)
        if method is None:
            # No Ozon call is made for this scope — because we never make one in execution
            # either. Checking a permission for a call we do not perform proves nothing.
            return _result(VerificationOutcome.VERIFICATION_UNSUPPORTED,
                           f"ozon.unsupported.{context.scope}")

        if not context.ozon_client_id:
            # Ozon authenticates with a PAIR. Without the Client-Id the credential is
            # incomplete, and there is nothing to send — so no request is made.
            return _result(VerificationOutcome.INVALID_CREDENTIALS,
                           f"ozon.missing_client_id.{context.scope}")

        # ── 1. do these credentials work at all? ────────────────────────────
        info_key = f"ozon.seller_info.{context.scope}"
        info = await transport.request(ProbeRequest(
            method="POST", url=f"{_SELLER_BASE}{_SELLER_INFO_PATH}",
            headers=_headers(context), json={},
        ))

        if info.status != 200:
            return _classify_error(info, info_key)

        company = info.json.get("company") if isinstance(info.json, dict) else None
        if not isinstance(company, dict) or not company:
            # A 200 whose body we do not recognise is not a success we can claim.
            return _result(VerificationOutcome.SCHEMA_MISMATCH, info_key, http_status=200,
                           response_schema_status="unparseable" if info.json is None
                           else "missing_fields")
        # `company` carries the seller's INN, OGRN and legal name. None of it is read,
        # returned or stored: this is credential verification, not identity discovery.

        # ── 2. may this key call the method the scope actually needs? ───────
        roles_key = f"ozon.roles.{context.scope}"
        roles_resp = await transport.request(ProbeRequest(
            method="POST", url=f"{_SELLER_BASE}{_ROLES_PATH}",
            headers=_headers(context), json={},
        ))

        if roles_resp.status != 200:
            return _classify_error(roles_resp, roles_key)

        roles = roles_resp.json.get("roles") if isinstance(roles_resp.json, dict) else None
        covered = _role_covers(roles, method)

        if covered is None:
            return _result(VerificationOutcome.SCHEMA_MISMATCH, roles_key, http_status=200,
                           response_schema_status="missing_fields")

        if not covered:
            return _result(VerificationOutcome.MISSING_SCOPE, roles_key, http_status=200,
                           response_schema_status="ok")

        return _result(VerificationOutcome.VERIFIED, roles_key, http_status=200,
                       response_schema_status="ok")
