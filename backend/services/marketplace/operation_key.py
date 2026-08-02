"""SECURITY-2D-1B-B — operation KEY (identity) construction + validation (pure, no FastAPI/DB).

The operation KEY answers "WHICH business operation is this" — a stable, immutable id, NEVER derived
from content (price / bid / product / campaign / payload / fingerprint / content hash). The request
FINGERPRINT (see request_fingerprint.py) answers WHAT the operation does. Two legitimate operations
with identical content at different times are NOT a retry and must carry different keys.

Namespaces (v1):
    v1:client:<uuid4>    manual direct command — client-minted, one UUID per user intent
    v1:decision:<id>     applying a Decision — Decision.id (server-derived)
    v1:review:<id>       publishing a review reply — review.id (server-derived)
    v1:revert:<id>       reverting a prior action — original ExecutionLog.id (server-derived)
    v1:intent:<uuid4>    RESERVED — no producer in 1B-B
"""
from __future__ import annotations

import re

_PREFIX = "v1:"
MAX_KEY_LEN = 120

# Canonical lowercase hyphenated UUID version 4 (variant 8/9/a/b). Uppercase, whitespace, non-v4,
# overlong and malformed all fail this exact match.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
# A whole v1 operation key: v1:<ns>:<canonical lowercase hyphenated UUID>. The tail of EVERY namespace
# must be a real UUID — client UUIDs are v4 (router-validated); Decision.id / review.id / ExecutionLog.id
# are all str(uuid4). A non-UUID tail (v1:decision:abc, v1:review:x, …) is rejected, so a malformed
# server-derived key can never reach the provider.
_UUID_TAIL = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_V1_KEY_RE = re.compile(r"^v1:(client|decision|review|revert|intent):" + _UUID_TAIL + r"$")


class OperationKeyError(Exception):
    """Raised for an invalid / missing / forbidden operation key. `code` is a stable domain code."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


def is_valid_v1_key(key: str | None) -> bool:
    """True iff `key` is a well-formed v1 operation key that fits the column."""
    return (
        isinstance(key, str)
        and len(key) <= MAX_KEY_LEN
        and _V1_KEY_RE.match(key) is not None
    )


def canonical_client_uuid(raw: str | None) -> str:
    """Validate a client-supplied Idempotency-Key value: canonical lowercase hyphenated UUID v4.

    Rejects None (→ OPERATION_KEY_REQUIRED) and whitespace / uppercase / non-v4 / malformed / overlong
    (→ OPERATION_KEY_INVALID). Returns the bare uuid string (not the namespaced key). The key is never
    logged and is not an authorization token.
    """
    if raw is None:
        raise OperationKeyError("OPERATION_KEY_REQUIRED", "operation key required")
    if not isinstance(raw, str):
        raise OperationKeyError("OPERATION_KEY_INVALID", "operation key malformed")
    if raw != raw.strip() or len(raw) != 36 or _UUID4_RE.match(raw) is None:
        raise OperationKeyError(
            "OPERATION_KEY_INVALID", "operation key must be a canonical lowercase UUIDv4"
        )
    return raw


def client_key(raw: str | None) -> str:
    """`v1:client:<uuid4>` from a validated client-supplied UUID. The client never sends the prefix."""
    return _PREFIX + "client:" + canonical_client_uuid(raw)


def decision_key(decision_id: str) -> str:
    return _PREFIX + "decision:" + decision_id


def review_key(review_id: str) -> str:
    return _PREFIX + "review:" + review_id


def revert_key(original_log_id: str) -> str:
    return _PREFIX + "revert:" + original_log_id


def forbid_body_key(value) -> None:
    """A deprecated body idempotency_key on an executable route is forbidden — the header is the ONLY
    manual-identity source. Any present value (even one matching the header) is rejected; no fallback,
    no silent ignore, no 'header wins'."""
    if value is not None:
        raise OperationKeyError(
            "BODY_OPERATION_KEY_FORBIDDEN",
            "operation key must be sent as the Idempotency-Key header, not in the body",
        )
