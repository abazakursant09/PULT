"""Pure operation-fingerprint inputs + computation (no FastAPI / DB / provider / executor imports).

Extracted VERBATIM from the executor in SECURITY-2D-1C-C3A so a read-only consumer (the operator recovery
view) can recompute the canonical request fingerprint of a stored ExecutionLog row WITHOUT importing the
executor (which pulls provider clients / dispatch). The logic is byte-identical to the pre-extraction
executor helpers, so every stored fingerprint (and the fp1 goldens) is unchanged; the executor now
re-exports these names for its callers.

The KEY (identity) is NOT computed here — this describes only WHAT an operation does (its contents).
"""
from __future__ import annotations

from decimal import Decimal

from .request_fingerprint import request_fingerprint

# Volatile evidence excluded from the fingerprint (present at execute time, not part of the operation's
# stable "what"): prior price/cpm, step, prior card snapshot, provenance, rating.
_FP_VOLATILE = {"old_price", "old_cpm", "step_pct", "old_card", "insight_key", "rating"}


def _money(v):
    """Money/price/cpm → a scale-preserving string (never a float — the fp1 helper rejects floats)."""
    if v is None or isinstance(v, str) or isinstance(v, bool):
        return v                                # bool is an int subclass — keep it distinct, not money
    if isinstance(v, (float, Decimal)):
        return str(Decimal(str(v)))
    return v                                    # int kept exact


def _clean_floats(obj):
    """Recursively convert any float to a scale-preserving string; list order preserved."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return str(Decimal(str(obj)))
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_floats(v) for v in obj]
    return obj


def _fp_inputs(action_type: str, payload: dict) -> tuple[dict, dict]:
    """(target, params) for the fingerprint — curated per action, volatile evidence excluded, money
    normalized to strings. The KEY (identity) is NOT here; this describes only WHAT the op does."""
    p = payload or {}
    if action_type == "set_price":
        return {"offer_id": p.get("offer_id")}, {"price": _money(p.get("price"))}
    if action_type == "publish_review_response":
        return {"feedback_id": p.get("feedback_id")}, {"text": p.get("text")}
    if action_type == "ad_set_bid":
        return ({"campaign_id": p.get("campaign_id"), "adv_type": p.get("adv_type")},
                {"cpm": _money(p.get("cpm"))})
    if action_type == "ad_set_state":
        return {"campaign_id": p.get("campaign_id")}, {"action": p.get("action")}
    if action_type == "update_card":
        return {"offer_id": p.get("offer_id")}, {"card": _clean_floats(p.get("card"))}
    # generic (decision overrides / /execute): exclude known-volatile keys, normalize floats.
    params = {k: _clean_floats(v) for k, v in p.items()
              if k not in _FP_VOLATILE and k != "marketplace"}
    return {}, params


def compute_fingerprint(user_id, connection_id, marketplace, action_type, mode, payload,
                        reverted_from) -> str:
    """Canonical fp1 fingerprint of an operation's contents. Identical output to the former
    executor._fingerprint (which now re-exports this)."""
    target, params = _fp_inputs(action_type, payload)
    return request_fingerprint(
        user_id=user_id, connection_id=connection_id, marketplace=marketplace,
        action_type=action_type, mode=mode, target=target, params=params,
        reverted_from=reverted_from,
    )
