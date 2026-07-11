"""Connection-level verification rollup (F1.2b-a).

The rollup is derived from ONE source: the verification_status values PERSISTED on the
connection's ApiCredential rows. Never from an attempt outcome, an HTTP status, or an
error category — those are evidence, not state, and a temporary outcome (a timeout, a
rate limit, a marketplace outage) is deliberately never allowed to reach persisted state
in the first place. Reading them here would smuggle them back in through the side door
and let a Wildberries outage flip a seller's connection to "broken".

So: temporary outcomes cannot influence this function, because by construction they never
changed anything it reads.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from models.api_credential import ApiCredential

UNVERIFIED = "unverified"
VERIFIED = "verified"

# Explicit precedence among PERMANENT failure states. Ordered by how loudly the seller
# needs to act, most severe first — never by lexical order, which would put
# `invalid_credentials` ahead of `ownership_conflict` for no reason at all.
#
#   ownership_conflict  — the cabinet belongs to another workspace. Nothing the seller
#                         does to this connection can fix it; it outranks everything.
#   invalid_credentials — the secret is wrong. A new secret is required.
#   revoked             — the secret was withdrawn at the marketplace. Also requires a
#                         new secret, but it is a different story to tell.
#   missing_scope       — the secret is fine; it just does not grant this scope.
#   tariff_restricted   — the secret and scope are fine; the seller's plan forbids it.
FAILURE_PRECEDENCE: tuple[str, ...] = (
    "ownership_conflict",
    "invalid_credentials",
    "revoked",
    "missing_scope",
    "tariff_restricted",
)


def rollup_status(credentials: Iterable[ApiCredential]) -> str:
    """Connection verification_status from the persisted per-scope states."""
    states = [c.verification_status for c in credentials]

    if not states:
        # Nothing stored: nothing has been verified. Not a failure — just no evidence.
        return UNVERIFIED

    for failure in FAILURE_PRECEDENCE:
        if failure in states:
            return failure

    if all(s == VERIFIED for s in states):
        return VERIFIED

    # At least one scope is still unverified: the connection as a whole is not verified.
    return UNVERIFIED


def rollup_verified_at(credentials: Iterable[ApiCredential]) -> Optional[datetime]:
    """Connection verified_at — set only when every stored scope is verified.

    The LATEST credential verified_at, because the connection only became fully verified
    at the moment its last outstanding scope did. Taking the earliest would claim the
    connection was proven at a time when part of it demonstrably was not.
    """
    creds = list(credentials)
    if rollup_status(creds) != VERIFIED:
        return None

    stamps = [c.verified_at for c in creds if c.verified_at is not None]
    return max(stamps) if stamps else None
