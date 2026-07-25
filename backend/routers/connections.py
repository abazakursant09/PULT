"""Marketplace cabinet connections (ME-1). Stores encrypted API tokens."""
import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.automation_rule import AutomationRule
from models.user import User
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.marketplace import (
    ConnectionCreate, ConnectionOut, ScopeVerificationOut, VerifyOut, VerifyRequest,
)
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.yandex_client import yandex_client
from services.marketplace.verification import runner as verification_runner
from services.marketplace.verification import service as verification_service
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id

log = logging.getLogger(__name__)
router = APIRouter()

# Yandex joined once it became verifiable (F1.2d): its Partner API exposes a documented
# read-only token-introspection call, so a Yandex connection can be checked rather than
# merely believed. PULT executes nothing on Yandex — there is no client in the executor —
# so this is a read connection, and that is exactly what F1 is for.
_VALID_MP = {"wildberries", "ozon", "yandex"}
_VALID_SCOPES = {"feedbacks", "prices", "advert", "content", "stocks", "promotions"}

# A marketplace whose credential is a PAIR names the second half here. Kept as data so the route
# stays free of marketplace branching (`marketplace` is a value, never a code path): a new
# marketplace with a compound credential is one entry, not another `if`.
_REQUIRED_CREDENTIAL_FIELD = {"ozon": "ozon_client_id"}

# Identity of a cabinet connected through this route is not yet verified: F1.1 connects
# cabinets, it does not discover them. `unverified_legacy` is reserved for rows the F1.1
# migration reconstructed from pre-F1.1 connections; both mean "external id unknown" and
# differ only in provenance. Discovery, in a later slice, is what may write `verified`.
_UNVERIFIED = "unverified"

# Credential verification (F1.2a). Same word, DIFFERENT question: `_UNVERIFIED` above asks
# "which cabinet is this?", this one asks "do these credentials work?". Both are unknown
# here — the route calls no marketplace — but they are answered by different future slices
# (discovery vs. a verification probe), so they are kept as separate constants.
_UNVERIFIED_CREDENTIALS = "unverified"


async def _disable_automation(db: AsyncSession, connection_id: str) -> None:
    """Disarm every automation rule bound to this connection. Flush-only — the caller commits.

    Called on BOTH disconnect and reconnect, and that repetition is the point: automatic publishing
    must never resume by itself after the credentials behind it changed. Whichever way a seller
    arrives back at a working connection, the switch is off and they turn it on themselves.

    Only `enabled` is touched. Consent and mode survive, because the seller really did consent and
    making them repeat the whole agreement for a key rotation would be a punishment for good
    hygiene.
    """
    await db.execute(
        update(AutomationRule)
        .where(AutomationRule.connection_id == connection_id,
               AutomationRule.enabled.is_(True))
        .values(enabled=False)
    )


class _ExternalIdentityConflict(Exception):
    """The real cabinet behind a verified key is already bound to another Account."""


async def _ozon_external_id(conn: MarketplaceConnection, credential) -> str | None:
    """Ozon's Client-Id IS the stable cabinet id — already on the connection, no call needed."""
    return (conn.ozon_client_id or "").strip() or None


async def _yandex_external_id(conn: MarketplaceConnection, credential) -> str | None:
    """The businessId behind the Yandex key, read once via the existing resolver."""
    token = credential_vault.decrypt(credential.secret_enc)
    try:
        return await yandex_client.resolve_business_id(token=token)
    except ExecutionError:
        # Verified, but the cabinet could not be resolved (ambiguous / unreachable). Do not guess.
        return None


# marketplace -> how to read its stable external cabinet id. A marketplace ABSENT here (e.g.
# Wildberries) has no such id and keeps external_account_id NULL. Kept as data so the route never
# branches on a marketplace as a code path — the same discipline as _REQUIRED_CREDENTIAL_FIELD.
_EXTERNAL_ID_RESOLVER = {
    "ozon":   _ozon_external_id,
    "yandex": _yandex_external_id,
}

# marketplaces with no stable external id, whose only cross-cabinet dedupe is the token fingerprint.
_FINGERPRINT_MARKETPLACES = {"wildberries"}


async def _capture_external_identity(db: AsyncSession, conn: MarketplaceConnection,
                                     credential) -> None:
    """Write the cabinet's stable external id onto its MarketplaceAccount, after a positive verify.

    Data-driven: a marketplace that exposes a stable id has a resolver in _EXTERNAL_ID_RESOLVER; one
    that does not (Wildberries) is simply absent and keeps external_account_id NULL — WB relies on
    the token fingerprint instead of a made-up id.

    Idempotent: an id already present is left as-is. A clash with another Account raises
    `_ExternalIdentityConflict`, which the caller turns into a safe 409. Nothing is merged.
    """
    resolver = _EXTERNAL_ID_RESOLVER.get(conn.marketplace)
    account_id = conn.marketplace_account_id
    if resolver is None or account_id is None:
        return

    external_id = await resolver(conn, credential)
    if not external_id:
        return

    account = (await db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.id == account_id)
    )).scalars().first()
    if account is None:
        return
    if account.external_account_id == external_id:
        return   # already captured — idempotent
    if account.external_account_id and account.external_account_id != external_id:
        # The account is already pinned to a DIFFERENT cabinet — never silently repoint it.
        raise _ExternalIdentityConflict()

    account.external_account_id = external_id
    account.identity_status = "verified"
    try:
        await db.flush()
    except IntegrityError:
        # uq_mp_account_mp_ext: this real cabinet is already on another Account.
        raise _ExternalIdentityConflict()
    await db.commit()


async def _to_out(db: AsyncSession, conn: MarketplaceConnection) -> ConnectionOut:
    """Serialize a connection together with its per-scope verification state."""
    credentials = (
        await db.execute(
            select(ApiCredential)
            .where(ApiCredential.connection_id == conn.id)
            .order_by(ApiCredential.scope)
        )
    ).scalars().all()

    out = ConnectionOut.model_validate(conn)
    out.scopes_verification = [
        ScopeVerificationOut.model_validate(c) for c in credentials
    ]
    return out


@router.get("/connections", response_model=List[ConnectionOut])
async def list_connections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(MarketplaceConnection)
            .where(MarketplaceConnection.user_id == current_user.id)
            .order_by(MarketplaceConnection.created_at.desc())
        )
    ).scalars().all()
    return [await _to_out(db, conn) for conn in rows]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
async def create_connection(
    body: ConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.marketplace not in _VALID_MP:
        raise HTTPException(422, f"unknown marketplace: {body.marketplace}")
    if body.scope not in _VALID_SCOPES:
        raise HTTPException(422, f"unknown scope: {body.scope}")
    if not body.token.strip():
        raise HTTPException(422, "token is required")
    # Some marketplaces authenticate with a PAIR, not a lone token — Ozon needs a Client-Id beside
    # the key, and a connection saved without it would look connected and fail at the very first
    # call, with the probe reporting invalid_credentials for what is really a missing field. The
    # requirement is DATA (_REQUIRED_CREDENTIAL_FIELD), not a branch: the router never asks "is this
    # Ozon?", only "what does this marketplace require?", so the next marketplace is one dict entry.
    required_field = _REQUIRED_CREDENTIAL_FIELD.get(body.marketplace)
    if required_field and not (getattr(body, required_field, None) or "").strip():
        raise HTTPException(422, f"{required_field} is required for {body.marketplace}")

    try:
        workspace_id = await resolve_workspace_id(db, current_user.id)
    except WorkspaceMissing:
        # Registration and the F1.0 backfill both guarantee a workspace, so this is a
        # broken lifecycle invariant, not something the caller did wrong or can fix.
        log.error("workspace missing for authenticated user")   # no user id in the log
        raise HTTPException(500, "connection could not be saved")

    # PULT-LAUNCH-1.4.5D: two entry shapes.
    #   * account-bound (marketplace_account_id given) — the seller picked a cabinet they already
    #     created (usually CSV-only) on the Stores screen. Attach to THAT account, never mint one.
    #   * legacy (no account_id) — the old Settings path: find-or-create by user+marketplace.
    if body.marketplace_account_id is not None:
        account = (
            await db.execute(
                select(MarketplaceAccount)
                .where(MarketplaceAccount.id == body.marketplace_account_id,
                       MarketplaceAccount.workspace_id == workspace_id)
            )
        ).scalars().first()
        # A foreign cabinet and a missing one return the SAME 404 — the reply must never reveal
        # that an id exists but belongs to someone else.
        if account is None:
            raise HTTPException(404, "cabinet not found")
        # The key must match the cabinet's marketplace; a WB key on an Ozon cabinet is a mistake,
        # not a silent re-typing of the cabinet.
        if account.marketplace != body.marketplace:
            raise HTTPException(422, "marketplace does not match the selected cabinet")

        # At most one connection per account (uq_mp_conn_account). Reuse it on reconnect, so the
        # cabinet keeps one identity and its history survives.
        conn = (
            await db.execute(
                select(MarketplaceConnection).where(
                    MarketplaceConnection.marketplace_account_id == account.id)
            )
        ).scalars().first()
        if conn is None:
            conn = MarketplaceConnection(
                id=str(uuid.uuid4()), user_id=current_user.id,
                marketplace=body.marketplace, label=body.label or account.label,
                status="connected", scopes=[body.scope], ozon_client_id=body.ozon_client_id,
                marketplace_account_id=account.id,
            )
            db.add(conn)
        else:
            if body.scope not in (conn.scopes or []):
                conn.scopes = [*(conn.scopes or []), body.scope]
            if body.label:
                conn.label = body.label
            if body.ozon_client_id:
                conn.ozon_client_id = body.ozon_client_id
            conn.status = "connected"
            conn.updated_at = datetime.utcnow()
        # No new MarketplaceAccount is ever created on this path, and the account's Store rows are
        # never touched — connecting an API key adds credentials, it does not reshape the cabinet.
    else:
        # ── Legacy Settings path (unchanged behaviour) ──────────────────────────────────────────
        conn = (
            await db.execute(
                select(MarketplaceConnection).where(
                    MarketplaceConnection.user_id == current_user.id,
                    MarketplaceConnection.marketplace == body.marketplace,
                )
            )
        ).scalars().first()

        if conn is None:
            conn = MarketplaceConnection(
                id=str(uuid.uuid4()),
                user_id=current_user.id,
                marketplace=body.marketplace,
                label=body.label,
                status="connected",
                scopes=[body.scope],
                ozon_client_id=body.ozon_client_id,
            )
            db.add(conn)
        else:
            if body.scope not in (conn.scopes or []):
                conn.scopes = [*(conn.scopes or []), body.scope]
            if body.label:
                conn.label = body.label
            if body.ozon_client_id:
                conn.ozon_client_id = body.ozon_client_id
            # Reconnecting a shop the seller had disconnected. `status` used to be written ONLY in
            # the branch above, so a revoked row stayed revoked forever: the new key was stored and
            # encrypted, the card kept saying "Отключён" with no buttons, and the automation gate
            # went on refusing it. There was no way out through the UI or the API.
            conn.status = "connected"
            conn.updated_at = datetime.utcnow()

        # Identity (F1.1): the cabinet keeps one MarketplaceAccount across every reconnect and token
        # rotation, so an account is minted only when the connection has none. `external_account_id`
        # stays NULL here: identity capture happens on verify, not on save.
        if conn.marketplace_account_id is None:
            account = MarketplaceAccount(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                marketplace=conn.marketplace,
                external_account_id=None,
                identity_status=_UNVERIFIED,
                label=conn.label,
            )
            db.add(account)
            conn.marketplace_account_id = account.id

    conn.workspace_id = workspace_id

    # A marketplace with no stable external id names itself here, so the route never branches on a
    # marketplace as a code path (it asks "does this marketplace need a fingerprint?", data, not
    # "is this WB?"). For such a marketplace a keyed fingerprint of the token is the only way to
    # notice the SAME key on a second cabinet. HMAC, never the token, never returned or logged.
    if body.marketplace in _FINGERPRINT_MARKETPLACES:
        conn.credential_fingerprint = credential_vault.fingerprint(body.token.strip())

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # Either the account already has a connection (uq_mp_conn_account) or this WB token is
        # already used by another cabinet (uq_mp_conn_fingerprint). Both are the same safe answer:
        # a 409 that names no other cabinet and no owner.
        raise HTTPException(409, "this API key or cabinet is already connected")

    # store/replace the encrypted token for this scope
    existing = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == conn.id,
                ApiCredential.scope == body.scope,
            )
        )
    ).scalars().first()
    enc = credential_vault.encrypt(body.token.strip())
    # Storing is not verifying: this route calls no marketplace. The secret it just wrote
    # has never been checked, so THIS scope goes back to unverified — and only this one.
    # Verification is per-scope, so a rotated `prices` token must not erase a `feedbacks`
    # credential that a probe genuinely confirmed.
    if existing:
        existing.secret_enc = enc
        existing.verification_status = _UNVERIFIED_CREDENTIALS
        existing.verified_at = None
        existing.updated_at = datetime.utcnow()
    else:
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope=body.scope, secret_enc=enc,
                             verification_status=_UNVERIFIED_CREDENTIALS, verified_at=None))

    await db.flush()
    # The connection's own status is a ROLLUP of the persisted per-scope states — never a
    # value written here directly, and never derived from an attempt outcome. No attempt is
    # recorded either: saving a credential is not an attempt to verify it.
    await verification_service.refresh_connection_rollup(db, conn)

    # The credentials behind this connection just changed — on a reconnect AND on a plain key
    # replacement. Automation stays off until the seller switches it on again: the key it would
    # publish with is not the key they approved it for, and it has not been verified yet either.
    # Same transaction as the credential write, so there is no window where a new secret is live
    # while an old rule is still armed.
    await _disable_automation(db, conn.id)

    await db.commit()
    await db.refresh(conn)
    log.info("connection saved: user=%s mp=%s scope=%s", current_user.id,
             body.marketplace, body.scope)  # token never logged
    return await _to_out(db, conn)


@router.post("/connections/{connection_id}/verify", response_model=VerifyOut)
async def verify_connection_scope(
    connection_id: str,
    body: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check ONE stored scope against its marketplace.

    Separate from `POST /connections` on purpose: saving a credential must not depend on a
    marketplace being reachable. WB's seller-info allows a Basic token one call per 24 h —
    tying storage to a probe would mean a rate-limited seller could not even save. This way
    a check can be retried without the seller re-entering the token.

    The router picks no marketplace and knows no probe: the runner resolves the adapter
    from the registry.
    """
    try:
        conn, credential, result = await verification_runner.verify_credential(
            db, user_id=current_user.id, connection_id=connection_id, scope=body.scope,
        )
    except verification_runner.ConnectionNotFound:
        raise HTTPException(404, "connection not found")

    log.info("verification: conn=%s mp=%s scope=%s outcome=%s",   # token never logged
             conn.id, conn.marketplace, body.scope, result.outcome.value)

    # Identity is captured ONLY on a real, positive verify (PULT-LAUNCH-1.4.5D). A stored key is
    # not a connected cabinet: the external id is written after the marketplace confirms the key,
    # never on save. A failed verify writes nothing here.
    if result.outcome.value == "verified" and credential is not None:
        try:
            await _capture_external_identity(db, conn, credential)
        except _ExternalIdentityConflict:
            # The real cabinet behind this key is already attached to another Account — mine or
            # someone else's; the reply cannot tell which. Same safe 409, no owner revealed.
            await db.rollback()
            raise HTTPException(409, "this cabinet is already connected")

    return VerifyOut(
        connection_id=conn.id,
        marketplace=conn.marketplace,
        scope=body.scope,
        outcome=result.outcome.value,
        verification_status=(credential.verification_status if credential else "unverified"),
        verified_at=(credential.verified_at if credential else None),
        connection_verification_status=conn.verification_status,
        connection_verified_at=conn.verified_at,
        retry_after_seconds=result.retry_after_seconds,
    )


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.id == connection_id,
                MarketplaceConnection.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if conn is None:
        raise HTTPException(404, "connection not found")
    conn.status = "revoked"
    # Delete the stored secrets (PULT-LAUNCH-1.4.5D). Revoking the connection stops it being used,
    # but the ciphertext lingering in the vault is a secret we no longer need — disconnect means the
    # key is gone. The connection row itself stays (revoked) so a reconnect reuses the SAME cabinet;
    # only the credentials are removed. The token fingerprint goes with them, so re-entering the key
    # later is a fresh, unconflicted save.
    await db.execute(delete(ApiCredential).where(ApiCredential.connection_id == conn.id))
    conn.credential_fingerprint = None
    # Switch this connection's automation OFF in the same transaction as the disconnect. A rule
    # left `enabled` would be armed and waiting: reconnect the shop later — or replace the key —
    # and automatic publishing resumes on its own, using credentials the seller never re-approved
    # it for. Turning automation back on has to be a decision they make again, out loud.
    #
    # Consent and mode are deliberately KEPT: the seller did give consent, and erasing it would
    # force them through the whole agreement again for what may be a five-minute key rotation.
    # `enabled` alone is what arms the automation, so `enabled` alone is what we drop.
    #
    # Account, Store, Product, ProductPlacement, imported CSV rows and review history are all left
    # untouched — disconnect removes the key, never the data the seller already has.
    await _disable_automation(db, conn.id)
    await db.commit()
