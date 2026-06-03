"""
Action catalog (RFC §3). Maps an `action_type` to the marketplace, the API
scope it needs, a payload validator, the dispatch coroutine, and whether it is
reversible. Adding a new seller action = adding one entry here. The executor
reads ONLY this catalog — it has no per-action branching.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .errors import ExecutionError
from .wb_client import wb_client
from .ozon_client import ozon_client


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    marketplace: str
    required_scope: str
    validate: Callable[[dict], None]
    dispatch: Callable[[str, dict], Awaitable[dict]]   # (token, payload) -> normalized result
    reversible: bool
    reverter: Callable[[dict, dict], tuple[str, dict]] | None = None  # (payload, result) -> (action_type, inverse_payload)


# ── validators ────────────────────────────────────────────────────────────────
def _require(payload: dict, *keys: str) -> None:
    missing = [k for k in keys if not payload.get(k)]
    if missing:
        raise ExecutionError(ExecutionError.VALIDATION, f"missing fields: {', '.join(missing)}")


def _validate_publish_review(payload: dict) -> None:
    _require(payload, "feedback_id", "text")
    if len(payload["text"]) > 5000:
        raise ExecutionError(ExecutionError.VALIDATION, "answer text too long (>5000)")


# ── dispatchers (normalize marketplace response) ───────────────────────────────
async def _dispatch_publish_review_wb(token: str, payload: dict) -> dict:
    resp = await wb_client.publish_feedback_answer(
        token=token, feedback_id=payload["feedback_id"], text=payload["text"]
    )
    return {
        "api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
        "published": True,
        "feedback_id": payload["feedback_id"],
    }


# ── registry ───────────────────────────────────────────────────────────────────
_CATALOG: dict[str, ActionSpec] = {
    "publish_review_response": ActionSpec(
        action_type="publish_review_response",
        marketplace="wildberries",
        required_scope="feedbacks",
        validate=_validate_publish_review,
        dispatch=_dispatch_publish_review_wb,
        reversible=False,  # a published public answer cannot be programmatically unpublished
    ),
    # next slices register here: set_price (ME-3), set_bid/pause (ME-4),
    # leave_promotion (ME-5), update_card (ME-6) — each with its validator,
    # WB/Ozon dispatcher, and (where the API allows) a reverter.
}


def get(action_type: str) -> ActionSpec:
    spec = _CATALOG.get(action_type)
    if spec is None:
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, f"no such action: {action_type}")
    return spec


def known_actions() -> list[str]:
    return sorted(_CATALOG)
