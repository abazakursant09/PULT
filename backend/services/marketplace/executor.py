"""
marketplace_executor — the single entry point for every seller action that
reaches a real marketplace (RFC §5). L3 (user one-click) and L4 (automation
rule) both flow through `execute()`. Nothing else may call a marketplace client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog

from . import action_catalog, guard, credential_vault
from .errors import ExecutionError

log = logging.getLogger(__name__)

_SECRET_KEYS = {"text"}  # payload keys safe to keep; secrets are never in payload anyway


@dataclass
class ExecutionResult:
    log_id: str | None
    status: str                     # success | failed | rejected | dry_run_ok
    action_type: str
    marketplace: str
    api_request_id: str | None = None
    result: dict = field(default_factory=dict)
    error: dict | None = None
    reversible: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("success", "dry_run_ok")


async def _resolve_connection(
    db: AsyncSession, user_id: str, marketplace: str, connection_id: str | None
) -> MarketplaceConnection:
    q = select(MarketplaceConnection).where(MarketplaceConnection.user_id == user_id)
    if connection_id:
        q = q.where(MarketplaceConnection.id == connection_id)
    else:
        q = q.where(MarketplaceConnection.marketplace == marketplace)
    conn = (await db.execute(q)).scalars().first()
    if conn is None:
        raise ExecutionError(ExecutionError.NO_CONNECTION, f"no {marketplace} connection")
    if conn.status != "connected":
        raise ExecutionError(ExecutionError.NO_CONNECTION, f"connection status={conn.status}")
    return conn


async def _resolve_token(db: AsyncSession, connection_id: str, scope: str) -> str:
    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == connection_id,
                ApiCredential.scope == scope,
            )
        )
    ).scalars().first()
    if cred is None:
        raise ExecutionError(ExecutionError.MISSING_SCOPE, f"no credential for scope '{scope}'")
    return credential_vault.decrypt(cred.secret_enc)


async def execute(
    *,
    db: AsyncSession,
    user_id: str,
    action_type: str,
    payload: dict,
    mode: str = "manual_l3",
    connection_id: str | None = None,
    insight_key: str | None = None,
    idempotency_key: str | None = None,
    rule: dict | None = None,
    dry_run: bool = False,
) -> ExecutionResult:
    spec = action_catalog.get(action_type)  # raises UNKNOWN_ACTION

    # Marketplace may be fixed by the spec, or carried in the payload for
    # marketplace-agnostic actions (e.g. set_price works for WB and Ozon).
    target_mp = spec.marketplace or payload.get("marketplace")

    # 1) resolve connection + 2) scope check
    try:
        if not target_mp and not connection_id:
            raise ExecutionError(
                ExecutionError.VALIDATION, "marketplace required for this action"
            )
        conn = await _resolve_connection(db, user_id, target_mp, connection_id)
        target_mp = conn.marketplace
        if spec.required_scope not in (conn.scopes or []):
            raise ExecutionError(
                ExecutionError.MISSING_SCOPE, f"connection lacks scope '{spec.required_scope}'"
            )
        # 3) validate payload
        spec.validate(payload)
        # 4) guard (before any network)
        await guard.check(
            db=db, user_id=user_id, action_type=action_type,
            payload=payload, mode=mode, rule=rule,
        )
    except ExecutionError as e:
        # rejected before any side effect; persist a rejected log for audit (not for dry_run)
        if dry_run:
            return ExecutionResult(None, "rejected", action_type, target_mp or "unknown", error=e.to_dict())
        rec = _new_log(user_id, action_type, target_mp, mode, payload,
                       insight_key, idempotency_key, status="rejected", error_code=e.code,
                       connection_id=connection_id)
        db.add(rec)
        await db.commit()
        return ExecutionResult(rec.id, "rejected", action_type, target_mp or "unknown", error=e.to_dict())

    if dry_run:
        return ExecutionResult(None, "dry_run_ok", action_type, target_mp,
                               result={"would_send": _safe_payload(payload)},
                               reversible=spec.reversible)

    ctx = {"marketplace": conn.marketplace, "ozon_client_id": conn.ozon_client_id}

    # 5) idempotency: return prior success instead of re-calling the API
    if idempotency_key:
        prior = (
            await db.execute(
                select(ExecutionLog).where(
                    ExecutionLog.user_id == user_id,
                    ExecutionLog.action_type == action_type,
                    ExecutionLog.idempotency_key == idempotency_key,
                    ExecutionLog.status == "success",
                )
            )
        ).scalars().first()
        if prior:
            return ExecutionResult(prior.id, "success", action_type, target_mp,
                                   api_request_id=prior.api_request_id,
                                   result=prior.result or {}, reversible=spec.reversible)

    # 6) write pending log BEFORE dispatch (crash visibility)
    rec = _new_log(user_id, action_type, target_mp, mode, payload,
                   insight_key, idempotency_key, status="pending", connection_id=conn.id)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)

    # 7) fetch token (vault) + 8) dispatch
    try:
        token = await _resolve_token(db, conn.id, spec.required_scope)
        result = await spec.dispatch(token, payload, ctx)
    except ExecutionError as e:
        rec.status = "failed"
        rec.error_code = e.code
        rec.finished_at = datetime.utcnow()
        await db.commit()
        log.warning("execution failed: user=%s action=%s code=%s", user_id, action_type, e.code)
        return ExecutionResult(rec.id, "failed", action_type, target_mp, error=e.to_dict())
    except Exception:  # noqa: BLE001 — a dispatcher bug must never become a 500
        rec.status = "failed"
        rec.error_code = "DISPATCH_ERROR"
        rec.finished_at = datetime.utcnow()
        await db.commit()
        log.exception("execution dispatch crashed: user=%s action=%s", user_id, action_type)
        return ExecutionResult(rec.id, "failed", action_type, target_mp,
                               error={"code": "DISPATCH_ERROR", "detail": "internal dispatch error", "retryable": False})

    # 9) persist success
    rec.status = "success"
    rec.api_request_id = result.get("api_request_id")
    rec.result = _safe_result(result)
    rec.finished_at = datetime.utcnow()
    await db.commit()
    log.info("execution success: user=%s action=%s mode=%s log=%s",
             user_id, action_type, mode, rec.id)
    return ExecutionResult(rec.id, "success", action_type, target_mp,
                           api_request_id=rec.api_request_id, result=rec.result,
                           reversible=spec.reversible)


async def revert(*, db: AsyncSession, user_id: str, log_id: str) -> ExecutionResult:
    """Issue the inverse of a prior successful, reversible action."""
    rec = (
        await db.execute(select(ExecutionLog).where(ExecutionLog.id == log_id,
                                                     ExecutionLog.user_id == user_id))
    ).scalars().first()
    if rec is None:
        raise ExecutionError(ExecutionError.VALIDATION, "log not found")
    spec = action_catalog.get(rec.action_type)
    if not spec.reversible or spec.reverter is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", f"{rec.action_type} cannot be reverted")
    inverse_action, inverse_payload = spec.reverter(rec.payload or {}, rec.result or {})
    res = await execute(db=db, user_id=user_id, action_type=inverse_action,
                        payload=inverse_payload, mode=rec.mode, connection_id=rec.connection_id)
    if res.log_id:
        # link the revert
        rv = (await db.execute(select(ExecutionLog).where(ExecutionLog.id == res.log_id))).scalars().first()
        if rv:
            rv.reverted_from = rec.id
            rec.status = "reverted"
            await db.commit()
    return res


# ── helpers ─────────────────────────────────────────────────────────────────
def _safe_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items()}  # payload never contains secrets by contract


def _safe_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "token"}


def _new_log(user_id, action_type, marketplace, mode, payload, insight_key,
             idempotency_key, *, status, error_code=None, connection_id=None) -> ExecutionLog:
    return ExecutionLog(
        user_id=user_id, connection_id=connection_id, insight_key=insight_key,
        action_type=action_type, marketplace=marketplace, mode=mode,
        payload=_safe_payload(payload), status=status, error_code=error_code,
        idempotency_key=idempotency_key,
    )
