"""SECURITY-2D-1B-A — canonical request fingerprint (pure helper, UNWIRED in 1B-A).

Architectural decision record (key vs fingerprint):
  * The idempotency KEY identifies WHICH business operation this is — a stable, immutable operation id
    (Decision.id, review.id, a client-generated per-action UUID, or the original ExecutionLog.id for a
    revert). A content hash MUST NEVER be used as the operation identity: two legitimate operations with
    identical content at different times (e.g. set price 1000 today, then set it 1000 again next month)
    are NOT a retry and must carry different operation ids.
  * The request FINGERPRINT (this module) describes WHAT the operation does — its contents. In 1B-B it is
    compared on a key collision: same key + different fingerprint => IDEMPOTENCY_MISMATCH (never routed
    to a different store/target), same key + same fingerprint => cached / needs_reconcile.
  * A new identical command must get a NEW operation id; a transport retry of the same command reuses the
    SAME operation id. This module does NOT create or infer operation ids — it only fingerprints contents.

Pure: no DB / network / provider / config imports. No secrets, tokens or headers are part of the
contract — only the eight declared fields below.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from decimal import Decimal
from typing import Any

_SCHEMA_VERSION = "fp1"
_PREFIX = "fp1:"


def _canonicalize(value: Any) -> Any:
    """Return a JSON-safe copy with deterministic types; validate fail-closed. Never mutates the input.

    bool stays bool (checked before int, since bool is an int subclass); int stays int; str is NFC
    normalized; None stays null; Decimal becomes its exact string (the caller owns scale/quantization —
    no arbitrary trailing-zero stripping); float is REJECTED at any depth; dict keys must be str; lists
    keep their order (the caller sorts order-insensitive lists before calling); any other type is
    rejected.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal NaN/Infinity is not allowed in a request fingerprint")
        return str(value)                       # preserve the caller's scale; no normalize()
    if isinstance(value, float):
        raise TypeError("float is not allowed in a request fingerprint — pass a Decimal with an "
                        "explicit scale, or a string")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError("request-fingerprint dict keys must be str")
            out[k] = _canonicalize(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]   # order preserved by design
    raise TypeError(f"unsupported type in request fingerprint: {type(value).__name__}")


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, no whitespace, no NaN/Infinity, floats rejected. Exposed
    for golden tests."""
    canonical = _canonicalize(obj)
    return json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def request_fingerprint(*, user_id: str, connection_id: str | None, marketplace: str | None,
                        action_type: str, mode: str, target: dict, params: dict,
                        reverted_from: str | None) -> str:
    """`"fp1:" + sha256(canonical_json(...))`. Every field is always present (None vs a missing key are
    distinct). `target`/`params` are caller-normalized dicts; `reverted_from` is the original
    ExecutionLog.id for a revert, else None. Volatile evidence (created_at, api_request_id, old_price,
    projected_margin, step_pct, …) MUST be excluded by the caller and is not part of the contract."""
    obj = {
        "schema_version": _SCHEMA_VERSION,
        "user_id": user_id,
        "connection_id": connection_id,
        "marketplace": marketplace,
        "action_type": action_type,
        "mode": mode,
        "target": target,
        "params": params,
        "reverted_from": reverted_from,
    }
    return _PREFIX + hashlib.sha256(canonical_json(obj)).hexdigest()


_HEXDIGEST_LEN = 64                       # sha256 hex
_FP_LEN = len(_PREFIX) + _HEXDIGEST_LEN   # "fp1:" + 64 = 68


def is_valid_fingerprint(fp: str | None) -> bool:
    """True iff `fp` is a well-formed canonical fingerprint: the exact "fp1:" prefix followed by 64
    lowercase hex chars (the shape produced by request_fingerprint()). Fail-closed: anything else — None,
    wrong length, wrong prefix, uppercase or non-hex — is invalid. Pure; logs nothing."""
    if not isinstance(fp, str) or len(fp) != _FP_LEN or not fp.startswith(_PREFIX):
        return False
    digest = fp[len(_PREFIX):]
    return all(c in "0123456789abcdef" for c in digest)
