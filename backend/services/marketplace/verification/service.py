"""Verification state machine (F1.2b-a). Records evidence, then projects state.

This slice is the marketplace-INDEPENDENT half: it knows how to classify and persist a
verification outcome, and nothing about how to obtain one. It performs no network call,
imports no marketplace client, and never decrypts a credential — the probe that will do
those things arrives in a later slice and hands its result here.

Order matters: the attempt is always recorded, and only then may state change. Evidence
first, verdict second — an outcome that moved state without leaving a trail would be
unexplainable afterwards.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_credential import ApiCredential
from models.connection_verification_attempt import ConnectionVerificationAttempt
from models.marketplace_connection import MarketplaceConnection

from . import projection
from .taxonomy import VerificationOutcome, VerificationResult, meta

ADAPTER_VERSION = "1"


class Verifier(Protocol):
    """A marketplace probe. Implemented in F1.2b-b (WB) and F1.2b-c (Ozon)."""

    async def verify(self, *, marketplace: str, scope: str) -> VerificationResult:
        ...


class NullVerifier:
    """No probe exists for this marketplace/scope yet.

    Returns `verification_unsupported` — which writes no state. It reports the absence of
    an answer; it never manufactures a successful one.
    """

    async def verify(self, *, marketplace: str, scope: str) -> VerificationResult:
        return VerificationResult(
            outcome=VerificationOutcome.VERIFICATION_UNSUPPORTED,
            probe_key=f"null.{marketplace}.{scope}",
            adapter_version=ADAPTER_VERSION,
        )


async def _credentials_for(db: AsyncSession, connection_id: str) -> list[ApiCredential]:
    return list((await db.execute(
        select(ApiCredential).where(ApiCredential.connection_id == connection_id)
    )).scalars().all())


async def refresh_connection_rollup(db: AsyncSession, conn: MarketplaceConnection) -> None:
    """Recompute the connection rollup from the persisted per-scope states."""
    credentials = await _credentials_for(db, conn.id)
    conn.verification_status = projection.rollup_status(credentials)
    conn.verified_at = projection.rollup_verified_at(credentials)


async def record_attempt(
    db: AsyncSession,
    *,
    connection: MarketplaceConnection,
    result: VerificationResult,
    credential: Optional[ApiCredential] = None,
    scope: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> ConnectionVerificationAttempt:
    """Append the attempt, apply its verdict if it has one, reproject the connection.

    A credential's state is touched ONLY when the outcome is marked `changes_state`. A
    timeout, a rate limit, a marketplace outage or a decryption failure leave every stored
    value exactly as it was: none of them is evidence about the secret, and treating them
    as such would invalidate working credentials during an outage or a key rotation.
    """
    now = now or datetime.utcnow()

    attempt = ConnectionVerificationAttempt(
        connection_id=connection.id,
        credential_id=credential.id if credential is not None else None,
        marketplace_account_id=connection.marketplace_account_id,
        marketplace=connection.marketplace,
        scope=scope if scope is not None else (credential.scope if credential else None),
        probe_key=result.probe_key,
        adapter_version=result.adapter_version,
        outcome=result.outcome.value,
        error_category=(result.error_category.value if result.error_category else None),
        http_status=result.http_status,
        retry_after_seconds=result.retry_after_seconds,
        response_schema_status=result.response_schema_status,
        started_at=started_at,
        finished_at=finished_at,
        created_at=now,
    )
    db.add(attempt)

    if credential is not None and meta(result.outcome).changes_state:
        credential.verification_status = result.outcome.value
        credential.verified_at = now if result.outcome is VerificationOutcome.VERIFIED else None

    await db.flush()
    await refresh_connection_rollup(db, connection)

    return attempt
