"""Deterministic hashing primitives for the Runtime Envelope (Sprint 73).

Pure, stdlib-only. No clocks, no randomness, no environment, no hidden state.
Canonicalization is byte-identical across processes, machines, and replays:
JSON with sorted keys, no insignificant whitespace, UTF-8, non-ASCII preserved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(payload: Any) -> bytes:
    """Canonical, order-independent serialization of a JSON-compatible payload.

    Dict key order does NOT affect the output (sort_keys=True). This is the only
    serialization used for hashing — every hash in this package goes through it.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(data: Any) -> str:
    """SHA-256 hex digest of a payload via canonical_bytes (or raw bytes/str)."""
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonical_bytes(data)
    return hashlib.sha256(raw).hexdigest()


def domain_hash(domain: str, payload: Any) -> str:
    """SHA-256 over a domain-separated payload.

    The domain tag prevents cross-purpose hash collisions (a boot hash can never
    equal a session hash even with identical payloads).
    """
    return sha256_hex(canonical_bytes({"__domain__": domain, "payload": payload}))
