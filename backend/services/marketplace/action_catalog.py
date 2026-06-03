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


# ── Advertising (ME-4) ─────────────────────────────────────────────────────────
def _validate_set_bid(payload: dict) -> None:
    _require(payload, "campaign_id", "cpm", "adv_type")
    try:
        if int(payload["cpm"]) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ExecutionError(ExecutionError.VALIDATION, "cpm must be a positive integer")


def _validate_set_state(payload: dict) -> None:
    _require(payload, "campaign_id", "action")
    if payload["action"] not in ("start", "pause"):
        raise ExecutionError(ExecutionError.VALIDATION, "action must be 'start' or 'pause'")


async def _dispatch_set_bid(token: str, payload: dict, ctx: dict) -> dict:
    mp = ctx.get("marketplace")
    if mp == "wildberries":
        resp = await wb_client.set_bid(
            token=token, campaign_id=int(payload["campaign_id"]),
            cpm=int(payload["cpm"]), adv_type=int(payload["adv_type"]),
            param=payload.get("param"),
        )
    elif mp == "ozon":
        resp = await ozon_client.set_bid(token=token, **payload)  # raises (ME-4b)
    else:
        raise ExecutionError(ExecutionError.VALIDATION, f"ad_set_bid: unsupported marketplace {mp}")
    return {"api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
            "campaign_id": payload["campaign_id"], "cpm": payload["cpm"]}


async def _dispatch_set_state(token: str, payload: dict, ctx: dict) -> dict:
    mp = ctx.get("marketplace")
    if mp != "wildberries":
        raise ExecutionError(ExecutionError.VALIDATION, f"ad_set_state: unsupported marketplace {mp}")
    resp = await wb_client.set_campaign_state(
        token=token, campaign_id=int(payload["campaign_id"]), action=payload["action"]
    )
    return {"api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
            "campaign_id": payload["campaign_id"], "state": payload["action"]}


def _revert_set_bid(payload: dict, result: dict) -> tuple[str, dict]:
    old = payload.get("old_cpm")
    if old is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", "no old_cpm recorded")
    return "ad_set_bid", {**payload, "cpm": old, "old_cpm": payload.get("cpm")}


def _revert_set_state(payload: dict, result: dict) -> tuple[str, dict]:
    inverse = "pause" if payload.get("action") == "start" else "start"
    return "ad_set_state", {**payload, "action": inverse}


# ── SEO / Content (ME-5) ───────────────────────────────────────────────────────
def _validate_update_card(payload: dict) -> None:
    _require(payload, "offer_id", "card")
    if not isinstance(payload["card"], dict) or not payload["card"]:
        raise ExecutionError(ExecutionError.VALIDATION, "card must be a non-empty object")


async def _dispatch_update_card(token: str, payload: dict, ctx: dict) -> dict:
    mp = ctx.get("marketplace")
    card = dict(payload["card"])
    if mp == "wildberries":
        card.setdefault("nmID", int(payload["offer_id"]))
        resp = await wb_client.update_card(token=token, card=card)
    elif mp == "ozon":
        resp = await ozon_client.update_card(token=token, **payload)  # raises (later slice)
    else:
        raise ExecutionError(ExecutionError.VALIDATION, f"update_card: unsupported marketplace {mp}")
    return {"api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
            "offer_id": payload["offer_id"], "updated": True}


def _revert_update_card(payload: dict, result: dict) -> tuple[str, dict]:
    old = payload.get("old_card")
    if not old:
        raise ExecutionError.guard("NOT_REVERSIBLE", "no old_card snapshot recorded")
    return "update_card", {"marketplace": payload.get("marketplace"),
                           "offer_id": payload["offer_id"], "card": old,
                           "old_card": payload.get("card")}


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
    "ad_set_bid": ActionSpec(
        action_type="ad_set_bid", marketplace=None, required_scope="advert",
        validate=_validate_set_bid, dispatch=_dispatch_set_bid,
        reversible=True, reverter=_revert_set_bid,
    ),
    "ad_set_state": ActionSpec(
        action_type="ad_set_state", marketplace=None, required_scope="advert",
        validate=_validate_set_state, dispatch=_dispatch_set_state,
        reversible=True, reverter=_revert_set_state,
    ),
    "update_card": ActionSpec(
        action_type="update_card", marketplace=None, required_scope="content",
        validate=_validate_update_card, dispatch=_dispatch_update_card,
        reversible=True, reverter=_revert_update_card,
    ),
}


def get(action_type: str) -> ActionSpec:
    spec = _CATALOG.get(action_type)
    if spec is None:
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, f"no such action: {action_type}")
    return spec


def known_actions() -> list[str]:
    return sorted(_CATALOG)
