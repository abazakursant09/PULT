"""
Action catalog (RFC §3). Maps an `action_type` to the marketplace, the API
scope it needs, a payload validator, the dispatch coroutine, and whether it is
reversible. Adding a new seller action = adding one entry here. The executor
reads ONLY this catalog — it has no per-action branching.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .errors import ExecutionError
from .wb_client import wb_client
from .ozon_client import ozon_client


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    marketplace: Optional[str]            # None = resolve from payload['marketplace'] (WB+Ozon action)
    required_scope: str
    validate: Callable[[dict], None]
    dispatch: Callable[[str, dict, dict], Awaitable[dict]]  # (token, payload, ctx) -> normalized result
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


def _validate_set_price(payload: dict) -> None:
    _require(payload, "offer_id", "price")
    try:
        if float(payload["price"]) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ExecutionError(ExecutionError.VALIDATION, "price must be a positive number")


# ── dispatchers (normalize marketplace response) ───────────────────────────────
async def _dispatch_publish_review(token: str, payload: dict, ctx: dict) -> dict:
    # ME-2 currently supports WB; Ozon Reviews is premium (ozon_client raises).
    resp = await wb_client.publish_feedback_answer(
        token=token, feedback_id=payload["feedback_id"], text=payload["text"]
    )
    return {
        "api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
        "published": True,
        "feedback_id": payload["feedback_id"],
    }


async def _dispatch_set_price(token: str, payload: dict, ctx: dict) -> dict:
    mp = ctx.get("marketplace")
    if mp == "wildberries":
        resp = await wb_client.set_price(
            token=token, offer_id=str(payload["offer_id"]),
            price=float(payload["price"]), discount=payload.get("discount"),
        )
    elif mp == "ozon":
        resp = await ozon_client.set_price(
            token=token, client_id=ctx.get("ozon_client_id"),
            offer_id=str(payload["offer_id"]), price=float(payload["price"]),
        )
    else:
        raise ExecutionError(ExecutionError.VALIDATION, f"set_price: unsupported marketplace {mp}")
    return {
        "api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
        "offer_id": payload["offer_id"],
        "new_price": payload["price"],
    }


def _revert_set_price(payload: dict, result: dict) -> tuple[str, dict]:
    """Inverse of a price change = set the recorded old_price back."""
    old = payload.get("old_price")
    if old is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", "no old_price recorded to revert to")
    return "set_price", {
        "marketplace": payload.get("marketplace"),
        "offer_id": payload["offer_id"],
        "price": old,
        "old_price": payload.get("price"),
    }


# ── registry ───────────────────────────────────────────────────────────────────
_CATALOG: dict[str, ActionSpec] = {
    "publish_review_response": ActionSpec(
        action_type="publish_review_response",
        marketplace="wildberries",
        required_scope="feedbacks",
        validate=_validate_publish_review,
        dispatch=_dispatch_publish_review,
        reversible=False,  # a published public answer cannot be programmatically unpublished
    ),
    "set_price": ActionSpec(
        action_type="set_price",
        marketplace=None,                 # WB or Ozon, resolved from payload/connection
        required_scope="prices",
        validate=_validate_set_price,
        dispatch=_dispatch_set_price,
        reversible=True,
        reverter=_revert_set_price,
    ),
    # next slices register here: ad_set_bid/ad_pause/ad_start (ME-4),
    # update_card (ME-5) — each with its validator, WB/Ozon dispatcher, reverter.
}


def get(action_type: str) -> ActionSpec:
    spec = _CATALOG.get(action_type)
    if spec is None:
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, f"no such action: {action_type}")
    return spec


def known_actions() -> list[str]:
    return sorted(_CATALOG)
