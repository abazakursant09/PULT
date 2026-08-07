"""SECURITY-2D-1C-C3A — pure, READ-ONLY projection helpers for the operator recovery view.

NO executor / provider / dispatch imports (an AST guard test enforces this). NO DB access here — every
function takes an already-loaded ExecutionLog row and returns plain data. Nothing in this module (or its
router) mutates a row, calls the executor, or reaches a provider. `supported_for_retry` is only a
PRELIMINARY read-only eligibility indicator computed from the stored row — NOT an authorization to send;
the real live re-check (connection/token/capability/consent/automation) belongs to a future C3C.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Optional, Tuple

from config import settings
from services.marketplace.operation_key import is_valid_v1_key
from services.marketplace.request_fingerprint import is_valid_fingerprint
from services.marketplace.operation_fingerprint import compute_fingerprint

# Actions whose ORIGINAL command can be reconstructed EXACTLY from the stored payload (curated target
# present). stop_auto_promotion is deliberately excluded — it is a contained action that never produces a
# safe pending claim and is fail-closed in the executor.
_SUPPORTED_ACTIONS = frozenset({
    "set_price", "publish_review_response", "ad_set_state", "ad_set_bid", "update_card", "reduce_discount",
})

# reconciliation_status values that make a row NOT a preliminary-safe candidate (under investigation or a
# current-state mismatch that never authorises a retry). Only None / 'pending_recon' stay eligible.
_BLOCKING_RECON = frozenset({
    "reconciling", "intent_observed", "target_not_observed", "still_unknown",
    "manual_attention", "resolved",
})

_TARGET_HMAC_PREFIX = "pult:recovery-target:v1:"


def curated_target(action_type: str, payload: Optional[dict]) -> Optional[dict]:
    """The strictly-allowlisted, stable target fields for a supported action, or None when a required
    field is missing / empty / the action is unsupported. NEVER returns raw payload or extra keys."""
    p = payload or {}

    def _nz(key):
        v = p.get(key)
        return v if v not in (None, "") else None

    if action_type in ("set_price", "update_card", "reduce_discount"):
        oid = _nz("offer_id")
        return {"offer_id": oid} if oid is not None else None
    if action_type == "publish_review_response":
        fid = _nz("feedback_id")
        return {"feedback_id": fid} if fid is not None else None
    if action_type == "ad_set_state":
        cid = _nz("campaign_id")
        return {"campaign_id": cid} if cid is not None else None
    if action_type == "ad_set_bid":
        cid = _nz("campaign_id")
        adv = _nz("adv_type")
        return {"campaign_id": cid, "adv_type": adv} if cid is not None else None
    return None


def target_reference(row) -> Optional[str]:
    """A short, NON-reversible, STABLE reference to the operation's target — never the raw provider id.

    Domain-separated HMAC-SHA256 keyed by settings.secret_key (NOT the operator key, so rotating the
    operator key does NOT change it). Same target → same reference; different target → different reference.
    None when the action is unsupported or the curated target is incomplete (raw target never leaks).
    """
    target = curated_target(row.action_type, row.payload)
    if target is None:
        return None
    key = (settings.secret_key or "").encode("utf-8")
    msg = (_TARGET_HMAC_PREFIX + (row.action_type or "")
           + "|" + json.dumps(target, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    digest = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return "tgt:" + digest[:12]


def evaluate_retry(row) -> Tuple[bool, str]:
    """Preliminary read-only eligibility of a stored row for a FUTURE safe re-dispatch. Returns
    (supported_for_retry, reason_code). Pure — no DB, no provider, no live re-check. reason_code is drawn
    from a small closed set and makes clear this is preliminary eligibility, not an authorization to send.
    """
    if row.manual_resolution is not None:
        return False, "eligibility_manually_resolved"
    if row.status != "pending":
        return False, "eligibility_not_pending"
    if row.dispatch_started_at is not None:
        # A pending row with a dispatch stamp may already have reached the provider → never safe.
        return False, "eligibility_already_dispatched"
    if (row.reconciliation_status or None) in _BLOCKING_RECON:
        return False, "eligibility_under_reconciliation"
    if row.action_type not in _SUPPORTED_ACTIONS:
        return False, "eligibility_unsupported_action"
    if not is_valid_v1_key(row.idempotency_key):
        return False, "eligibility_invalid_key"
    if not is_valid_fingerprint(row.request_fingerprint):
        return False, "eligibility_invalid_fingerprint"
    if curated_target(row.action_type, row.payload) is None:
        return False, "eligibility_incomplete_payload"
    # attempt_count > 0 on a pending+undispatched row is anomalous; reown_count bounds transfers. Both are
    # bounded by the same conservative config ceiling (no new C3A flag introduced).
    if row.reown_count is not None and row.reown_count >= settings.recovery_max_reowns:
        return False, "eligibility_limit_exceeded"
    if (row.attempt_count or 0) >= settings.recovery_max_reowns:
        return False, "eligibility_limit_exceeded"
    # Recompute the canonical fingerprint from the stored payload and require it to match what was stored
    # at claim time — proof the payload is intact and the row is exactly the operation it claims to be.
    recomputed = compute_fingerprint(
        row.user_id, row.connection_id, row.marketplace, row.action_type, row.mode,
        row.payload or {}, row.reverted_from,
    )
    if not hmac.compare_digest(recomputed, row.request_fingerprint):
        return False, "eligibility_fingerprint_mismatch"
    return True, "eligibility_safe_pending_preliminary"
