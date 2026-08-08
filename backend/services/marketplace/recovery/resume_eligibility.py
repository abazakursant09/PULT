"""SECURITY-2D-1C-C3C1 — READ-ONLY eligibility evaluation for a future operator authorize-and-resume.

This module ONLY reads and computes. It performs NO mutation (no ORM add, no UPDATE / INSERT / DELETE, no
commit, no flush, no ORM attribute write), NO provider call, NO token decrypt / OAuth / refresh / network,
and it never dispatches. It imports no provider client and never runs the executor's write / dispatch /
fencing paths. It is the internal preparation the future C3C2 authorize+fencing+dispatch will build on;
C3C1 wires NO endpoint and creates NO dispatch path. (C3A's `supported_for_retry` remains the external
preliminary indicator; this is the richer internal check.)

Returned data is deliberately safe: eligibility + a closed-set reason code + owned generation + action_type
+ mode + a token-preparation flag. NEVER the payload / operation key / fingerprint / token / credential /
raw target / user / connection / provider ids.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from config import settings
from models.execution_log import ExecutionLog
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services import capability_registry
from services.marketplace import action_catalog, guard
from services.marketplace.errors import ExecutionError
from services.marketplace.operation_key import is_valid_v1_key
from services.marketplace.request_fingerprint import is_valid_fingerprint
from services.marketplace.operation_fingerprint import compute_fingerprint
# Pure, dispatch-free helpers (the single source of truth for the capability map / marketplace canon /
# contained set). Importing them does NOT pull a provider client and cannot dispatch — a guard test
# forbids the dangerous write/dispatch/fencing CALLS (not this import), so the capability/contained rules
# never drift from the executor.
from services.marketplace.executor import capability_for_action, _canon_mp, _CONTAINED_ACTIONS

logger = logging.getLogger(__name__)

# Actions whose stored payload can be resumed EXACTLY (same set the C3A read-only view supports).
_SUPPORTED_ACTIONS = frozenset({
    "set_price", "publish_review_response", "ad_set_state", "ad_set_bid", "update_card", "reduce_discount",
})
# reconciliation_status values that block resume (under investigation or a current-state mismatch that is
# NOT proof the op was never dispatched). Only None / 'pending_recon' stay eligible.
_BLOCKING_RECON = frozenset({
    "reconciling", "intent_observed", "target_not_observed", "still_unknown",
    "manual_attention", "resolved",
})
# manual_resolution → eligible? None / confirmed_not_applied / manual_closed may be structurally allowed
# (the operator opinion itself proves nothing). confirmed_applied and retry_authorized are ineligible.
_MANUAL_INELIGIBLE = {
    "confirmed_applied": "manual_already_applied",
    "retry_authorized": "already_retry_authorized",
}

_REVERT_PREFIX = "v1:revert:"

# Closed set of safe reason codes (no PII / exception / provider text). Longest ≤ 40 chars.
_REASONS = frozenset({
    "preliminary_eligible", "token_preparation_required",
    "status_not_pending", "dispatch_already_started", "attempt_already_recorded",
    "invalid_operation_key", "invalid_fingerprint", "fingerprint_mismatch", "invalid_payload",
    "unsupported_action", "contained_action", "reown_limit_reached", "reconciliation_conflict",
    "manual_already_applied", "already_retry_authorized",
    "connection_missing", "connection_mismatch", "connection_disconnected",
    "credential_missing", "scope_missing", "capability_unavailable",
    "guard_rejected", "automation_disabled",
    "revert_original_missing", "revert_original_invalid", "cross_tenant",
})


@dataclass
class ResumeEligibility:
    eligible: bool
    reason_code: str
    owned_generation: Optional[int] = None
    action_type: Optional[str] = None
    mode: Optional[str] = None
    requires_live_token_resolution: bool = False


def _no(reason: str) -> ResumeEligibility:
    return ResumeEligibility(eligible=False, reason_code=reason)


def _structural(row: ExecutionLog, tenant_user_id: str) -> Optional[str]:
    """Pure structural checks (no DB, no network). Returns a reason code on failure, else None."""
    if row.user_id != tenant_user_id:
        return "cross_tenant"
    if row.status != "pending":
        return "status_not_pending"
    if row.dispatch_started_at is not None:
        return "dispatch_already_started"
    # A safe-pending claim whose fencing dispatch has NEVER been committed has attempt_count == 0. Any
    # value > 0 means a provider dispatch was already attempted → resume is never eligible.
    if (row.attempt_count or 0) != 0:
        return "attempt_already_recorded"
    if not is_valid_v1_key(row.idempotency_key):
        return "invalid_operation_key"
    # Structural payload shape BEFORE fingerprint checks: a corrupt non-dict payload is invalid_payload
    # regardless of what fingerprint (if any) is stored.
    if not isinstance(row.payload, dict):
        return "invalid_payload"
    if not is_valid_fingerprint(row.request_fingerprint):
        return "invalid_fingerprint"
    if row.action_type in _CONTAINED_ACTIONS:
        return "contained_action"
    if row.action_type not in _SUPPORTED_ACTIONS:
        return "unsupported_action"
    try:
        spec = action_catalog.get(row.action_type)
    except ExecutionError:
        return "unsupported_action"
    try:
        spec.validate(row.payload)
    except ExecutionError:
        return "invalid_payload"
    # Recompute the canonical fingerprint from the STORED payload and require an exact match — proof the
    # payload is intact and the row is exactly the operation it claims to be.
    recomputed = compute_fingerprint(row.user_id, row.connection_id, row.marketplace, row.action_type,
                                     row.mode, row.payload, row.reverted_from)
    if recomputed != row.request_fingerprint:
        return "fingerprint_mismatch"
    if row.reown_count is not None and row.reown_count >= settings.recovery_max_reowns:
        return "reown_limit_reached"
    if (row.reconciliation_status or None) in _BLOCKING_RECON:
        return "reconciliation_conflict"
    if row.manual_resolution in _MANUAL_INELIGIBLE:
        return _MANUAL_INELIGIBLE[row.manual_resolution]
    return None


async def _revert_original_ok(db, row: ExecutionLog, tenant_user_id: str) -> Optional[str]:
    """READ-ONLY validation of the linked original for a v1:revert inverse. Reason on failure, else None."""
    key = row.idempotency_key or ""
    if not key.startswith(_REVERT_PREFIX):
        return None                                  # not a revert inverse — nothing to check
    original_id = key[len(_REVERT_PREFIX):]
    original = (await db.execute(select(ExecutionLog).where(
        ExecutionLog.id == original_id, ExecutionLog.user_id == tenant_user_id))).scalars().first()
    if original is None:
        return "revert_original_missing"
    if original.status != "success":
        return "revert_original_invalid"             # only a succeeded original may be reverted
    try:
        ospec = action_catalog.get(original.action_type)
    except ExecutionError:
        return "revert_original_invalid"
    if not ospec.reversible or ospec.reverter is None:
        return "revert_original_invalid"
    # The pending inverse's action must be exactly what the original's reverter produces (read-only, pure).
    try:
        inverse_action, _inverse_payload = ospec.reverter(original.payload or {}, original.result or {})
    except ExecutionError:
        return "revert_original_invalid"
    if inverse_action != row.action_type:
        return "revert_original_invalid"
    return None


async def evaluate_resume(db, row: ExecutionLog, *, tenant_user_id: str,
                          live: bool = True) -> ResumeEligibility:
    """READ-ONLY: is this stored row a candidate for a future safe operator resume? Never mutates, never
    dispatches, never touches the network. `live=True` adds read-only DB checks of the current
    connection / credential / capability / guard / automation state."""
    reason = _structural(row, tenant_user_id)
    if reason is not None:
        return _no(reason)
    reason = await _revert_original_ok(db, row, tenant_user_id)
    if reason is not None:
        return _no(reason)

    spec = action_catalog.get(row.action_type)
    owned_generation = row.claim_generation

    if live:
        # An automated (L4) resume needs the automation kill-switch ON — checked FIRST (fail-fast, and it
        # mirrors the executor's early automation choke) so it is never masked by a later gate.
        if row.mode == "automated_l4" and settings.automation_enabled is not True:
            return _no("automation_disabled")
        # Connection binding (read-only).
        conn = (await db.execute(select(MarketplaceConnection).where(
            MarketplaceConnection.id == row.connection_id))).scalars().first()
        if conn is None:
            return _no("connection_missing")
        if conn.user_id != tenant_user_id:
            return _no("connection_mismatch")
        if row.marketplace is not None and conn.marketplace != row.marketplace:
            return _no("connection_mismatch")
        if conn.status != "connected":
            return _no("connection_disconnected")
        # Credential record + scope (read-only; NO decrypt, NO OAuth, NO network — real token preparation
        # is deferred to C3C2 right before its fencing CAS).
        cred = (await db.execute(select(ApiCredential).where(
            ApiCredential.connection_id == conn.id,
            ApiCredential.scope == spec.required_scope))).scalars().first()
        if cred is None:
            return _no("credential_missing")
        if spec.required_scope not in (conn.scopes or []):
            return _no("scope_missing")
        # Capability (pure registry lookup) — only for actions the registry gates.
        cap_key = capability_for_action(row.action_type)
        if cap_key is not None:
            avail = capability_registry.availability(cap_key, _canon_mp(row.marketplace))
            if not (avail.get("marketplace_api") and avail.get("pult_supported")):
                return _no("capability_unavailable")
        # Guard (proven read-only). A guard rejection is a safe, deterministic "no".
        try:
            await guard.check(db=db, user_id=row.user_id, action_type=row.action_type,
                              payload=row.payload, mode=row.mode, rule=None)
        except ExecutionError:
            return _no("guard_rejected")

    # All read-only checks pass. This is a PRELIMINARY yes: C3C2 must still resolve/prepare the token and
    # take the atomic authorize+fencing CAS before any dispatch (which is where the operator/redispatch
    # flags become a real authorization). C3C1 never prepares the token or dispatches.
    return ResumeEligibility(
        eligible=True, reason_code="preliminary_eligible", owned_generation=owned_generation,
        action_type=row.action_type, mode=row.mode, requires_live_token_resolution=True)
