"""Runs a verification and persists its verdict (F1.2b-b). Marketplace-agnostic.

The one place a credential is decrypted, and the one place an adapter is chosen. It knows
nothing about any marketplace: it resolves the target, hands the adapter a plaintext secret
and a transport, and gives the answer to the F1.2b-a service, which owns the rules that
decide whether an outcome may touch persisted state.

The probe runs OUTSIDE any write transaction. A network call inside one would hold a
database transaction open for as long as a marketplace takes to answer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_credential import ApiCredential
from models.marketplace_connection import MarketplaceConnection

from . import service
from .adapters import get_adapter
from .adapters.base import ProbeContext
from .taxonomy import VerificationOutcome, VerificationResult
from .transport import ProbeTransport, ProbeTransportError

SPINE_VERSION = "1"


class ConnectionNotFound(Exception):
    """No such connection for this owner. Carries no identifiers."""


async def _credential(db: AsyncSession, connection_id: str, scope: str
                      ) -> Optional[ApiCredential]:
    return (await db.execute(
        select(ApiCredential).where(
            ApiCredential.connection_id == connection_id,
            ApiCredential.scope == scope,
        )
    )).scalars().first()


async def _connection(db: AsyncSession, user_id: str, connection_id: str
                      ) -> MarketplaceConnection:
    conn = (await db.execute(
        select(MarketplaceConnection).where(
            MarketplaceConnection.id == connection_id,
            MarketplaceConnection.user_id == user_id,   # owner-scoped, always
        )
    )).scalars().first()
    if conn is None:
        raise ConnectionNotFound("connection not found")
    return conn


def _spine_result(outcome: VerificationOutcome, probe_key: str) -> VerificationResult:
    return VerificationResult(outcome=outcome, probe_key=probe_key,
                              adapter_version=SPINE_VERSION)


async def verify_credential(
    db: AsyncSession,
    *,
    user_id: str,
    connection_id: str,
    scope: str,
    transport: Optional[ProbeTransport] = None,
) -> tuple[MarketplaceConnection, Optional[ApiCredential], VerificationResult]:
    """Probe one stored credential and persist the verdict. Returns (conn, credential, result)."""
    transport = transport or ProbeTransport()
    conn = await _connection(db, user_id, connection_id)

    credential = await _credential(db, conn.id, scope)
    if credential is None:
        # Nothing stored for this scope: there is nothing to check, and no probe to run.
        result = _spine_result(VerificationOutcome.MISSING_SCOPE, f"spine.no_credential.{scope}")
        await service.record_attempt(db, connection=conn, credential=None, scope=scope,
                                     result=result)
        await db.commit()
        return conn, None, result

    adapter = get_adapter(conn.marketplace)
    if adapter is None:
        # No probe exists for this marketplace yet (Yandex today). Honest silence.
        result = _spine_result(VerificationOutcome.VERIFICATION_UNSUPPORTED,
                               f"spine.no_adapter.{conn.marketplace}")
        await service.record_attempt(db, connection=conn, credential=credential, scope=scope,
                                     result=result)
        await db.commit()
        return conn, credential, result

    # The single decryption boundary in the whole verification path.
    try:
        secret = credential_secret(credential)
    except ValueError:
        # Our key cannot read our ciphertext — almost always a changed CRED_ENC_KEY. This
        # says nothing about the seller's token, so it must not touch persisted state, and
        # no marketplace is called.
        result = _spine_result(VerificationOutcome.DECRYPT_FAILURE, f"spine.decrypt.{scope}")
        await service.record_attempt(db, connection=conn, credential=credential, scope=scope,
                                     result=result)
        await db.commit()
        return conn, credential, result

    # Evidence for detecting a replacement that lands while we are out on the network.
    probed_id = credential.id
    probed_updated_at = credential.updated_at

    context = ProbeContext(
        secret=secret,
        marketplace=conn.marketplace,
        scope=scope,
        ozon_client_id=conn.ozon_client_id,
        credential_meta=dict(credential.meta or {}),
    )

    started_at = datetime.utcnow()
    try:
        result = await adapter.verify(context, transport)
    except ProbeTransportError as exc:
        # Transport failure is marketplace-independent, so the spine classifies it — an
        # adapter never has to reimplement "the network broke".
        outcome = (VerificationOutcome.TIMEOUT if exc.kind == ProbeTransportError.TIMEOUT
                   else VerificationOutcome.MARKETPLACE_UNAVAILABLE)
        result = _spine_result(outcome, f"spine.transport.{scope}")
    finally:
        del secret          # the plaintext leaves scope with the request that needed it
    finished_at = datetime.utcnow()

    # Race: the seller may have replaced this secret while the probe was in flight. The
    # verdict we hold describes the OLD secret, so applying it would state something we
    # never tested. The attempt is still recorded, attributed to the credential and carrying
    # its real outcome — it is true evidence of what the marketplace said, and `started_at`
    # against the credential's `updated_at` shows plainly that the secret moved underneath
    # it. Only the state change is withheld. Re-classifying the attempt as some other
    # outcome would be the actual lie: the marketplace did answer, and it answered that.
    await db.refresh(credential)
    stale = (credential.id != probed_id) or (credential.updated_at != probed_updated_at)

    await service.record_attempt(
        db, connection=conn, credential=credential, scope=scope, result=result,
        started_at=started_at, finished_at=finished_at,
        apply_state=not stale,
    )

    await db.commit()
    return conn, credential, result


def credential_secret(credential: ApiCredential) -> str:
    """Decrypt. Isolated so the vault import stays out of the module's public flow."""
    from services.marketplace import credential_vault
    return credential_vault.decrypt(credential.secret_enc)
