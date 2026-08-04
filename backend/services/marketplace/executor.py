"""
marketplace_executor — the single entry point for every seller action that
reaches a real marketplace (RFC §5). L3 (user one-click) and L4 (automation
rule) both flow through `execute()`. Nothing else may call a marketplace client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select, text, bindparam
from sqlalchemy import DateTime as _SA_DateTime, Integer as _SA_Integer, String as _SA_String
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog

from config import settings
from services import capability_registry

from . import action_catalog, guard, credential_vault, operation_key
# SECURITY-2D-1C-C3A — the pure fingerprint helpers were relocated to operation_fingerprint (no executor
# import cycle) so a read-only consumer can recompute a stored row's fingerprint WITHOUT importing this
# module. Re-exported here UNCHANGED so every existing `executor._money` / `_fingerprint` / `_fp_inputs`
# caller keeps working and the fp1 goldens are byte-identical.
from .operation_fingerprint import (  # noqa: F401 — re-exported for backward-compatible callers
    _FP_VOLATILE, _money, _clean_floats, _fp_inputs, compute_fingerprint as _fingerprint,
)
from .errors import ExecutionError
from .ozon_performance_auth import PERFORMANCE_SCOPE

log = logging.getLogger(__name__)

# SECURITY-2D-1C-C1 — fencing ownership CAS. The pending→in_flight transition is a SINGLE atomic guarded
# UPDATE: it takes in_flight (and stamps dispatch_started_at + increments attempt_count) ONLY while the
# row is still an un-dispatched claim (status='pending', dispatch_started_at IS NULL) that THIS worker
# owns (claim_generation = the generation on its own claim). A worker that lost ownership to a future
# controlled re-own (which increments claim_generation) matches nothing → RETURNING is empty → it makes
# ZERO provider calls. No SELECT→UPDATE, no Python read-modify-write. Binds are typed so it runs on real
# PostgreSQL (asyncpg) as well as SQLite.
_FENCE_CAS = text(
    "UPDATE execution_logs "
    "SET status='in_flight', dispatch_started_at=:now, "
    "    attempt_count=attempt_count+1, last_attempt_at=:now "
    "WHERE id=:id AND status='pending' AND dispatch_started_at IS NULL "
    "  AND claim_generation=:gen "
    "RETURNING id, status, dispatch_started_at, attempt_count, last_attempt_at, claim_generation"
).bindparams(
    bindparam("now", type_=_SA_DateTime(timezone=True)),
    bindparam("gen", type_=_SA_Integer()),
    bindparam("id", type_=_SA_String()),
)

# action_type → capability_registry key (existing vocabulary only). Actions with
# no registry write-capability key (set_price, update_card) are intentionally
# UNMAPPED → legacy behavior preserved (no capability gate, no regression).
_ACTION_CAPABILITY = {
    # ad_set_bid is a LEGACY-ONLY execute path (insight_decision_bridge:
    # high_ad_spend -> ad_set_bid). campaign_control here is a legacy-SHARED gate:
    # bid-write and campaign on/off are semantically different operations, but the
    # legacy path reuses this capability. The CANONICAL registry must NOT make
    # ad_set_bid executable without a separate doctrine/Phase-0 decision (cpm has no
    # observed-derivable payload — see Canonical Surface Doctrine). A dedicated
    # campaign_bid / ad_bid_write capability is intentionally deferred to Phase-0
    # (legacy<->canonical consolidation), NOT this hardening slice. Do not remap here.
    "ad_set_bid":              "campaign_control",
    "ad_set_state":            "campaign_control",
    "publish_review_response": "review_reply",
    "reduce_discount":         "discounts.write",     # A2: WB/Ozon api, Yandex impossible
    "stop_auto_promotion":     "promotions.write",    # A3: WB/Ozon api, Yandex impossible
}

# Connection marketplace label → registry marketplace code.
_CANON_MP = {
    "wildberries": "wb", "wb": "wb",
    "ozon": "ozon",
    "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex",
}


def capability_for_action(action_type: str) -> str | None:
    """Capability registry key gating this write action, or None when unmapped."""
    return _ACTION_CAPABILITY.get(action_type)


def _canon_mp(mp: str | None) -> str:
    return _CANON_MP.get((mp or "").lower(), (mp or "").lower())


# ── 2.1A CONTAINMENT ──────────────────────────────────────────────────────────
# `stop_auto_promotion` promised an AUTOMATIC EXIT from an auto-promotion, but no marketplace
# path PULT has wired actually delivers that: Wildberries' /api/v1/promotions/participation is
# unconfirmed against the official API, Ozon's /v1/actions/products/deactivate is an ORDINARY-action
# opt-out (NOT Hot Sale / auto-promotion), and Yandex has no participation write API. Until a
# dedicated, provider-verified exit exists (2.2), the action is fail-closed HERE — before any
# connection, token or marketplace call — so a stale saved recommendation, a hand-built payload,
# an idempotent replay or a revert can never reach a provider. Strictly 0 marketplace calls.
_CONTAINED_ACTIONS = frozenset({"stop_auto_promotion"})

_CONTAINED_DETAIL = {
    "wb": "Автоматический выход из автоакции Wildberries через API пока не поддерживается. "
          "Выйдите из акции вручную в личном кабинете.",
    "ozon": "Автоматический выход из автоакции Ozon через API пока не поддерживается. "
            "Выйдите из акции вручную в личном кабинете.",
    "yandex": "Автоматический выход из акции Яндекс Маркета через API пока не поддерживается. "
              "Выйдите из акции вручную в личном кабинете.",
}
_CONTAINED_DEFAULT = ("Автоматический выход из автоакции через API пока не поддерживается. "
                      "Выйдите из акции вручную в личном кабинете.")


def _contained_error(marketplace: str | None) -> ExecutionError:
    detail = _CONTAINED_DETAIL.get(_canon_mp(marketplace), _CONTAINED_DEFAULT)
    return ExecutionError(ExecutionError.CAPABILITY_NOT_SUPPORTED, detail)


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


async def _account_ref(db: AsyncSession, connection_id: str, scope: str) -> str | None:
    """The account/cabinet id cached on this credential during sync, if the provider needed one.

    Non-secret, so it is read straight from `meta` — the vault boundary is only for the secret. A
    marketplace that scopes calls to an account (Yandex addresses reviews by cabinet) must publish
    into the same account it read from, so the two paths share one stored value.
    """
    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == connection_id,
                ApiCredential.scope == scope,
            )
        )
    ).scalars().first()
    return (cred.meta or {}).get("account_ref") if cred is not None else None


async def execute(
    *,
    db: AsyncSession,
    user_id: str,
    action_type: str,
    payload: dict,
    mode: str = "manual_l3",
    connection_id: str | None = None,
    insight_key: str | None = None,
    decision_id: str | None = None,
    idempotency_key: str | None = None,
    reverted_from: str | None = None,
    rule: dict | None = None,
    dry_run: bool = False,
) -> ExecutionResult:
    spec = action_catalog.get(action_type)  # raises UNKNOWN_ACTION

    # Marketplace may be fixed by the spec, or carried in the payload for
    # marketplace-agnostic actions (e.g. set_price works for WB and Ozon).
    target_mp = spec.marketplace or payload.get("marketplace")

    # 2.1A fail-closed: a contained action is rejected BEFORE any connection, token, guard,
    # idempotency replay or dispatch. Honest Russian reason; a rejected log for audit (never a
    # dry_run false success). No marketplace client is ever touched on this path.
    if action_type in _CONTAINED_ACTIONS:
        err = _contained_error(target_mp)
        if dry_run:
            return ExecutionResult(None, "rejected", action_type, target_mp or "unknown",
                                   error=err.to_dict())
        rec = _new_log(user_id, action_type, target_mp, mode, payload, insight_key,
                       idempotency_key, status="rejected", error_code=err.code,
                       connection_id=connection_id, decision_id=decision_id)
        db.add(rec)
        await db.commit()
        return ExecutionResult(rec.id, "rejected", action_type, target_mp or "unknown",
                               error=err.to_dict())

    # SECURITY-2D-1A — last-resort kill switch for AUTONOMOUS execution. An automated (L4) action
    # requires settings.automation_enabled to be exactly True; the check lives HERE, inside the single
    # executor entry point, BEFORE any connection / token / capability / guard / idempotency / dispatch,
    # so no caller (scheduler, task, decision_apply, a direct internal call, an automated retry, or a
    # revert of an automated action — which re-enters execute() with the ORIGINAL stored mode, execution
    # _log.mode) can reach a provider while automation is off. Fail-closed: `is not True` blocks False /
    # None / any non-True config value. Manual L3 is NOT gated by this flag — it is user-initiated and
    # gated by auth / consent / capability / guard. dry_run returns an HONEST non-executable rejection
    # (never a green "would send" preview); a rejected log is written for audit (never a dry_run false
    # success), carrying no provider token and only the already-secret-free payload.
    if mode == "automated_l4" and settings.automation_enabled is not True:
        err = ExecutionError.guard("AUTOMATION_DISABLED",
                                   "автоматическое исполнение отключено")
        if dry_run:
            return ExecutionResult(None, "rejected", action_type, target_mp or "unknown",
                                   error=err.to_dict())
        rec = _new_log(user_id, action_type, target_mp, mode, payload, insight_key,
                       idempotency_key, status="rejected", error_code=err.code,
                       connection_id=connection_id, decision_id=decision_id)
        db.add(rec)
        await db.commit()
        return ExecutionResult(rec.id, "rejected", action_type, target_mp or "unknown",
                               error=err.to_dict())

    # 1) resolve connection + 2) scope check
    try:
        if not target_mp and not connection_id:
            raise ExecutionError(
                ExecutionError.VALIDATION, "marketplace required for this action"
            )
        conn = await _resolve_connection(db, user_id, target_mp, connection_id)
        target_mp = conn.marketplace
        # Ozon campaign_control authenticates via Performance OAuth (resolved in
        # dispatch), not a static scoped token — its grant/credential is
        # advert_performance. Every other action keeps the existing scoped path,
        # so the WB path is byte-identical.
        ozon_perf = (capability_for_action(action_type) == "campaign_control"
                     and _canon_mp(target_mp) == "ozon")
        effective_scope = PERFORMANCE_SCOPE if ozon_perf else spec.required_scope
        if effective_scope not in (conn.scopes or []):
            raise ExecutionError(
                ExecutionError.MISSING_SCOPE, f"connection lacks scope '{effective_scope}'"
            )
        # 3) validate payload
        spec.validate(payload)
        # 3b) capability gate (A1): consult the registry before any write. Honest
        # CapabilityNotSupported instead of a random downstream marketplace error.
        # Unmapped actions (set_price, update_card) skip the gate (legacy behavior).
        cap_key = capability_for_action(action_type)
        if cap_key is not None:
            # Gate on the two facts PULT owns: the marketplace exposes the capability
            # (marketplace_api) AND PULT has built the integration (pult_supported). A capability the
            # marketplace can't do (verdict impossible) or PULT hasn't built (e.g. Yandex review
            # reply) fails closed here, before any write. The seller's marketplace TARIFF
            # (e.g. Ozon reviews need premium_plus) is deliberately NOT gated here — PULT cannot know
            # the seller's account tier, and the marketplace enforces it, returning an honest 4XX at
            # call time rather than a misleading local CapabilityNotSupported. availability defaults
            # pult_supported True + marketplace_api from the verdict, so every shipped action is
            # unaffected. (R-OZ2 introduced availability(); R-OZ3 splits capability from tariff.)
            avail = capability_registry.availability(cap_key, _canon_mp(target_mp))
            if not (avail.get("marketplace_api") and avail.get("pult_supported")):
                raise ExecutionError(
                    ExecutionError.CAPABILITY_NOT_SUPPORTED,
                    f"{action_type} not supported on {target_mp} (capability {cap_key}, {avail.get('status')})",
                )
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
                       connection_id=connection_id, decision_id=decision_id)
        db.add(rec)
        await db.commit()
        return ExecutionResult(rec.id, "rejected", action_type, target_mp or "unknown", error=e.to_dict())

    if dry_run:
        return ExecutionResult(None, "dry_run_ok", action_type, target_mp,
                               result={"would_send": _safe_payload(payload)},
                               reversible=spec.reversible)

    # SECURITY-2D-1B-B — operation key is MANDATORY on every executable (non-dry-run) path and must be a
    # well-formed v1 identity (client/decision/review/revert). A manual route with no Idempotency-Key,
    # or an automated_l4 caller with no server-owned key, is rejected HERE — 0 provider calls. The key is
    # never derived from content.
    if not operation_key.is_valid_v1_key(idempotency_key):
        err = ExecutionError.guard("OPERATION_KEY_REQUIRED", "operation key required")
        rec = _new_log(user_id, action_type, target_mp, mode, payload, insight_key,
                       None, status="rejected", error_code=err.code,
                       connection_id=conn.id, decision_id=decision_id)
        db.add(rec)
        await db.commit()
        return ExecutionResult(rec.id, "rejected", action_type, target_mp, error=err.to_dict())

    # request fingerprint = WHAT this operation does (contents); the KEY = WHICH operation.
    fp = _fingerprint(user_id, conn.id, target_mp, action_type, mode, payload, reverted_from)

    # Legacy-transition guard: a pre-1B-B row for the SAME provable identity (review.id / decision.id)
    # blocks a new dispatch regardless of its status — old rows have no trustworthy dispatch_started_at,
    # so we cannot prove the provider was not already called. 0 provider calls, no new claim, no leak.
    if await _legacy_alias_hit(db, user_id, idempotency_key, decision_id):
        return ExecutionResult(None, "needs_reconcile", action_type, target_mp,
                               error={"code": "LEGACY_OPERATION_NEEDS_RECONCILE",
                                      "detail": "операция найдена в прежнем формате — проверьте кабинет",
                                      "retryable": False},
                               reversible=spec.reversible)

    # Claim-before-dispatch: INSERT a pending row; the partial-UNIQUE(user_id, v1-key) lets exactly one
    # concurrent request win. The loser gets IntegrityError → rollback → resolve against the winner.
    rec = _new_log(user_id, action_type, target_mp, mode, payload, insight_key,
                   idempotency_key, status="pending", connection_id=conn.id,
                   decision_id=decision_id, request_fingerprint=fp)
    db.add(rec)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()   # MANDATORY before any SELECT — the aborted txn must be rolled back first
        existing = (
            await db.execute(
                select(ExecutionLog).where(
                    ExecutionLog.user_id == user_id,
                    ExecutionLog.idempotency_key == idempotency_key,
                )
            )
        ).scalars().first()
        return _resolve_existing(existing, fp, spec, action_type, target_mp)
    await db.refresh(rec)
    # SECURITY-2D-1C-C1 — the generation THIS worker owns is the one on its own freshly-created claim.
    # Captured here and never re-read right before the CAS: re-reading could adopt a generation a
    # concurrent re-own just bumped, defeating the fence.
    owned_generation = rec.claim_generation
    # Capture the log id into a local BEFORE the CAS: a CAS-phase rollback EXPIRES the ORM instance, so
    # reading rec.id afterwards would trigger a sync lazy-load (MissingGreenlet on async drivers) and mask
    # the safe needs_reconcile. The id is immutable, so the local is always correct.
    log_id = rec.id

    # Resolve the dispatch context + token BEFORE the fencing CAS, so between the CAS commit and the
    # provider call there are ZERO further DB reads. A credential/token failure here happens while the row
    # is still an un-dispatched pending claim (no provider call) and is recorded as a clean 'failed'.
    try:
        account_ref = await _account_ref(db, conn.id, spec.required_scope)
        # Ozon campaign_control resolves its bearer in-dispatch (Performance OAuth); no static token.
        token = None if ozon_perf else await _resolve_token(db, conn.id, spec.required_scope)
    except ExecutionError as e:
        rec.status = "failed"
        rec.error_code = e.code
        rec.finished_at = datetime.utcnow()
        await db.commit()
        log.warning("execution pre-dispatch failed: user=%s action=%s code=%s",
                    user_id, action_type, e.code)
        return ExecutionResult(rec.id, "failed", action_type, target_mp, error=e.to_dict())
    ctx = {"marketplace": conn.marketplace, "ozon_client_id": conn.ozon_client_id,
           "db": db, "connection_id": conn.id, "account_ref": account_ref}

    # FENCING CAS — take in_flight ONLY if we still own the un-dispatched claim (status pending,
    # dispatch_started_at NULL, our generation). RETURNING empty → ownership lost → ZERO provider calls.
    # The commit completes BEFORE dispatch, so a crash after this point is provably post-claim
    # (in_flight, dispatch_started_at set) and is never auto-retried.
    now = datetime.now(timezone.utc)
    try:
        fenced = (await db.execute(
            _FENCE_CAS, {"id": rec.id, "now": now, "gen": owned_generation})).first()
        await db.commit()
    except SQLAlchemyError:
        # A CAS-phase DB error (lock timeout, connection loss, a failing commit) is fail-closed: the CAS
        # neither committed in_flight nor dispatched, so the row is already a safe un-dispatched pending
        # claim. Roll back BEST-EFFORT — some async-driver error states cannot roll back cleanly on the
        # same connection (the session is discarded by its caller's context manager, which returns the
        # connection and drops the uncommitted CAS); a secondary rollback error must NEVER mask the safe
        # needs_reconcile return. Provider dispatch stays structurally unreachable (0 calls).
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 — never surface a secondary teardown error over the safe result
            pass
        log.warning("execution fencing CAS infra error: user=%s action=%s", user_id, action_type)
        return ExecutionResult(log_id, "needs_reconcile", action_type, target_mp,
                               error={"code": "CLAIM_CAS_ERROR",
                                      "detail": "не удалось подтвердить владение операцией — повторите позже",
                                      "retryable": False},
                               reversible=spec.reversible)
    if fenced is None:
        # Lost ownership between claim and CAS → never dispatch, never write a terminal/reconciliation
        # field, never create a second log; the row stays owned by whoever holds the current generation.
        return _reconcile(log_id, action_type, target_mp, spec, "OPERATION_IN_PROGRESS",
                          "операция уже выполняется")
    # Sync the ORM identity to the committed CAS state WITHOUT DB I/O (set_committed_value does not mark
    # the columns dirty), so the later terminal commit writes only status/result/finished_at and can never
    # clobber the CAS-set dispatch_started_at / attempt_count / last_attempt_at / claim_generation.
    set_committed_value(rec, "status", "in_flight")
    set_committed_value(rec, "dispatch_started_at", now)
    set_committed_value(rec, "attempt_count", fenced.attempt_count)
    set_committed_value(rec, "last_attempt_at", now)

    # 8) dispatch — NO DB query between the CAS commit above and this provider call.
    try:
        result = await spec.dispatch(token, payload, ctx)
    except ExecutionError as e:
        # AR5: a TIMEOUT / 5XX after the request left is AMBIGUOUS — the write may have committed,
        # so it is recorded distinctly and never auto-repeated (idempotency step above returns
        # needs_reconcile for it). Every other error is a clean "failed", safe to retry.
        ambiguous = ExecutionError.is_ambiguous_error(e.code)
        rec.status = "ambiguous" if ambiguous else "failed"
        rec.error_code = e.code
        rec.finished_at = datetime.utcnow()
        await db.commit()
        log.warning("execution %s: user=%s action=%s code=%s",
                    rec.status, user_id, action_type, e.code)
        return ExecutionResult(rec.id, "ambiguous" if ambiguous else "failed",
                               action_type, target_mp, error=e.to_dict())
    except Exception:  # noqa: BLE001 — a dispatcher bug must never become a 500
        rec.status = "failed"
        rec.error_code = "DISPATCH_ERROR"
        rec.finished_at = datetime.utcnow()
        await db.commit()
        log.exception("execution dispatch crashed: user=%s action=%s", user_id, action_type)
        return ExecutionResult(rec.id, "failed", action_type, target_mp,
                               error={"code": "DISPATCH_ERROR", "detail": "internal dispatch error", "retryable": False})

    # 9) persist success. SECURITY-2D-1B-B — when this is the inverse of a revert (reverted_from set),
    # the inverse's terminal success, its reverted_from link, AND the original's status='reverted' are
    # written in ONE commit. It is therefore structurally impossible to observe a succeeded inverse whose
    # original is still 'success': either all three land or none do. A terminal-commit failure after the
    # provider call leaves the inverse at in_flight and the original unchanged, and a later retry hits the
    # v1:revert claim (in_flight) → needs_reconcile, never a second dispatch.
    rec.status = "success"
    rec.api_request_id = result.get("api_request_id")
    rec.result = _safe_result(result)
    rec.finished_at = datetime.utcnow()
    if reverted_from:
        rec.reverted_from = reverted_from
        original = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.id == reverted_from,
            ExecutionLog.user_id == user_id))).scalars().first()
        if original is not None and original.status == "success":
            original.status = "reverted"
    await db.commit()
    log.info("execution success: user=%s action=%s mode=%s log=%s",
             user_id, action_type, mode, rec.id)
    return ExecutionResult(rec.id, "success", action_type, target_mp,
                           api_request_id=rec.api_request_id, result=rec.result,
                           reversible=spec.reversible)


async def revert(*, db: AsyncSession, user_id: str, log_id: str) -> ExecutionResult:
    """Issue the inverse of a prior successful, reversible action, exactly once per original."""
    rec = (
        await db.execute(select(ExecutionLog).where(ExecutionLog.id == log_id,
                                                     ExecutionLog.user_id == user_id))
    ).scalars().first()
    if rec is None:
        raise ExecutionError(ExecutionError.VALIDATION, "log not found")
    # SECURITY-2D-1B-B — an inverse may run only against a genuinely-succeeded, not-yet-reverted
    # original. This blocks reverting a failed/ambiguous/rejected/pending original (never happened, or
    # unknown) and double-reverting an already-reverted one.
    if rec.status != "success":
        raise ExecutionError.guard("NOT_REVERTIBLE_STATUS",
                                   f"cannot revert an action with status={rec.status}")
    spec = action_catalog.get(rec.action_type)
    if not spec.reversible or spec.reverter is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", f"{rec.action_type} cannot be reverted")
    inverse_action, inverse_payload = spec.reverter(rec.payload or {}, rec.result or {})
    # The inverse claims its OWN key in a separate namespace (never collides with the original execute
    # key); two concurrent reverts of the same original → one wins the claim, the other → needs_reconcile,
    # so at most one inverse provider dispatch happens. Original mode is passed back so the 2D-1A
    # automation choke still applies to the inverse of an automated action.
    # The inverse's success, its reverted_from link, and original.status='reverted' are committed
    # ATOMICALLY inside execute() (single terminal commit) — revert() adds no second commit, so the
    # "inverse succeeded but original still success" window is structurally impossible. A rejected /
    # failed / ambiguous inverse never reaches that success path, so the original stays as-is.
    res = await execute(db=db, user_id=user_id, action_type=inverse_action,
                        payload=inverse_payload, mode=rec.mode, connection_id=rec.connection_id,
                        idempotency_key=operation_key.revert_key(rec.id), reverted_from=rec.id)
    return res


# ── helpers ─────────────────────────────────────────────────────────────────
def _safe_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items()}  # payload never contains secrets by contract


def _safe_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "token"}


# ── SECURITY-2D-1B-B — legacy-alias / claim-resolve helpers ───────────────────
# (the pure fingerprint helpers now live in operation_fingerprint and are re-exported at the top of this
#  module: _FP_VOLATILE, _money, _clean_floats, _fp_inputs, _fingerprint.)


async def _legacy_alias_hit(db, user_id, op_key, decision_id) -> bool:
    """True iff a pre-1B-B row for the SAME provable identity exists (ANY status — old rows have no
    trustworthy dispatch_started_at). Only v1:review / v1:decision have a legacy predecessor."""
    if op_key.startswith("v1:review:"):
        rid = op_key[len("v1:review:"):]
        row = (await db.execute(
            select(ExecutionLog.id).where(
                ExecutionLog.user_id == user_id,
                ExecutionLog.action_type == "publish_review_response",
                ExecutionLog.idempotency_key == "review:" + rid,
            ).limit(1)
        )).first()
        return row is not None
    if op_key.startswith("v1:decision:"):
        did = op_key[len("v1:decision:"):]
        row = (await db.execute(
            select(ExecutionLog.id).where(
                ExecutionLog.user_id == user_id,
                ExecutionLog.decision_id == did,
                or_(ExecutionLog.idempotency_key.is_(None),
                    ExecutionLog.idempotency_key.notlike("v1:%")),
            ).limit(1)
        )).first()
        return row is not None
    return False


def _reconcile(log_id, action_type, target_mp, spec, code, detail) -> ExecutionResult:
    return ExecutionResult(log_id, "needs_reconcile", action_type, target_mp,
                           error={"code": code, "detail": detail, "retryable": False},
                           reversible=spec.reversible)


def _resolve_existing(existing, fp, spec, action_type, target_mp) -> ExecutionResult:
    """Decide the response when the claim INSERT lost the UNIQUE race. NEVER dispatches."""
    if existing is None:
        return _reconcile(None, action_type, target_mp, spec, "TRANSIENT_CONFLICT",
                          "конкурентная операция — повторите позже")
    efp = existing.request_fingerprint
    if efp is None:
        return _reconcile(existing.id, action_type, target_mp, spec, "NEEDS_RECONCILE",
                          "содержимое прежней операции не подтверждено")
    if efp != fp:
        return _reconcile(existing.id, action_type, target_mp, spec, "IDEMPOTENCY_MISMATCH",
                          "тот же ключ операции с другим содержимым")
    st = existing.status
    if st == "success":
        return ExecutionResult(existing.id, "success", action_type, target_mp,
                               api_request_id=existing.api_request_id,
                               result=existing.result or {}, reversible=spec.reversible)
    if st in ("pending", "in_flight"):
        return _reconcile(existing.id, action_type, target_mp, spec, "OPERATION_IN_PROGRESS",
                          "операция уже выполняется")
    if st == "ambiguous":
        return _reconcile(existing.id, action_type, target_mp, spec, "AMBIGUOUS_PRIOR",
                          "предыдущая попытка не подтверждена — проверьте кабинет")
    if st == "reverted":
        return _reconcile(existing.id, action_type, target_mp, spec, "ALREADY_REVERTED",
                          "операция уже отменена")
    # failed / any other terminal: controlled safe re-own is 1C — 1B-B never auto-retries.
    return _reconcile(existing.id, action_type, target_mp, spec, "PRIOR_FAILED",
                      "предыдущая попытка не удалась — начните новую операцию")


def _new_log(user_id, action_type, marketplace, mode, payload, insight_key,
             idempotency_key, *, status, error_code=None, connection_id=None,
             decision_id=None, request_fingerprint=None,
             dispatch_started_at=None) -> ExecutionLog:
    # SECURITY-2D-1B-B — only the pending CLAIM row carries the operation key + fingerprint, so it is
    # the sole occupant of the partial-UNIQUE(user_id, idempotency_key WHERE 'v1:%'). A rejected /
    # contained / automation-disabled row never dispatched and needs no dedup identity → NULL key, so
    # a later legitimate attempt with the same deterministic key can still claim.
    claim = status == "pending"
    return ExecutionLog(
        user_id=user_id, connection_id=connection_id, insight_key=insight_key,
        decision_id=decision_id,
        action_type=action_type, marketplace=marketplace, mode=mode,
        payload=_safe_payload(payload), status=status, error_code=error_code,
        idempotency_key=idempotency_key if claim else None,
        request_fingerprint=request_fingerprint if claim else None,
        dispatch_started_at=dispatch_started_at if claim else None,
    )
