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
from . import ozon_performance_auth

# ad_set_state action vocabulary (start|pause) → Ozon Performance verbs.
_OZON_STATE_VERB = {"start": "activate", "pause": "deactivate"}


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
def _validate_reduce_discount(payload: dict) -> None:
    # reduce_discount is ONE operation: lower/remove the WB discount. offer_id + a
    # numeric discount are required; never a price (that would be set_price).
    _require(payload, "offer_id")
    if payload.get("discount") is None:
        raise ExecutionError(ExecutionError.VALIDATION, "reduce_discount requires 'discount'")


async def _dispatch_reduce_discount(token: str, payload: dict, ctx: dict) -> dict:
    # reduce_discount = change the discount %, a Wildberries-only operation. It must
    # NEVER fall back to set_price (Ozon has no discount API → honest unavailable).
    mp = ctx.get("marketplace")
    if mp != "wildberries":
        raise ExecutionError(ExecutionError.CAPABILITY_NOT_SUPPORTED,
                             f"reduce_discount: {mp} has no discount API (Wildberries only)")
    resp = await wb_client.set_discount(
        token=token, offer_id=str(payload["offer_id"]), discount=float(payload["discount"]),
    )
    return {
        "api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
        "offer_id": payload["offer_id"], "discount": payload["discount"],
    }


def _revert_reduce_discount(payload: dict, result: dict) -> tuple[str, dict]:
    """Inverse = restore the recorded prior WB discount."""
    old = payload.get("old_discount")
    if old is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", "no old_discount recorded")
    return "reduce_discount", {"marketplace": payload.get("marketplace"),
                               "offer_id": payload["offer_id"],
                               "discount": old, "old_discount": payload.get("discount")}


def _validate_stop_auto_promotion(payload: dict) -> None:
    _require(payload, "offer_id")


async def _dispatch_stop_auto_promotion(token: str, payload: dict, ctx: dict) -> dict:
    mp = ctx.get("marketplace")
    enabled = bool(payload.get("enabled", False))   # stop → disable participation
    if mp == "wildberries":
        resp = await wb_client.set_auto_promotion(
            token=token, offer_id=str(payload["offer_id"]), enabled=enabled)
    elif mp == "ozon":
        resp = await ozon_client.set_auto_promotion(
            token=token, client_id=ctx.get("ozon_client_id"),
            offer_id=str(payload["offer_id"]), enabled=enabled)
    else:
        raise ExecutionError(ExecutionError.VALIDATION,
                             f"stop_auto_promotion: unsupported marketplace {mp}")
    return {
        "api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
        "offer_id": payload["offer_id"], "enabled": enabled,
    }


def _revert_stop_auto_promotion(payload: dict, result: dict) -> tuple[str, dict]:
    """Inverse = restore the recorded prior participation state (re-enable)."""
    old = payload.get("old_enabled")
    if old is None:
        raise ExecutionError.guard("NOT_REVERSIBLE", "no old_enabled recorded")
    return "stop_auto_promotion", {
        "marketplace": payload.get("marketplace"), "offer_id": payload["offer_id"],
        "enabled": bool(old), "old_enabled": payload.get("enabled"),
    }


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
    if mp == "ozon":
        return await _dispatch_set_state_ozon(payload, ctx)
    if mp != "wildberries":
        raise ExecutionError(ExecutionError.VALIDATION, f"ad_set_state: unsupported marketplace {mp}")
    resp = await wb_client.set_campaign_state(
        token=token, campaign_id=int(payload["campaign_id"]), action=payload["action"]
    )
    return {"api_request_id": (resp or {}).get("requestId") if isinstance(resp, dict) else None,
            "campaign_id": payload["campaign_id"], "state": payload["action"]}


async def _dispatch_set_state_ozon(payload: dict, ctx: dict) -> dict:
    """Ozon campaign state via Performance API + OAuth bearer (A2.2-pre-b.4).

    Bearer is resolved from the advert_performance credential through the cached
    OAuth helper — never from the executor's generic token, never logged. On a 401
    (expired bearer) the cache is invalidated and a fresh bearer is tried exactly
    once; no infinite retry."""
    db = ctx.get("db")
    connection_id = ctx.get("connection_id")
    if db is None or not connection_id:
        raise ExecutionError(ExecutionError.NO_CONNECTION,
                             "ad_set_state: Ozon dispatch requires connection context")
    verb = _OZON_STATE_VERB[payload["action"]]   # start→activate, pause→deactivate
    campaign_id = int(payload["campaign_id"])

    bearer = await ozon_performance_auth.acquire_bearer(db, connection_id=connection_id)
    try:
        resp = await ozon_client.set_campaign_state(token=bearer, campaign_id=campaign_id, action=verb)
    except ExecutionError as e:
        if e.code != ExecutionError.AUTH:
            raise
        # expired/invalid bearer → refresh ONCE and retry ONCE
        bearer = await ozon_performance_auth.acquire_bearer(
            db, connection_id=connection_id, force_refresh=True)
        resp = await ozon_client.set_campaign_state(token=bearer, campaign_id=campaign_id, action=verb)
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
    "reduce_discount": ActionSpec(        # margin alternative; measured on net_profit
        action_type="reduce_discount",
        marketplace="wildberries",        # discount API is WB-only; others honest-unavailable
        required_scope="prices",
        validate=_validate_reduce_discount,
        dispatch=_dispatch_reduce_discount,
        reversible=True,
        reverter=_revert_reduce_discount,
    ),
    "stop_auto_promotion": ActionSpec(    # A3 — margin alternative; measured on net_profit
        action_type="stop_auto_promotion",
        marketplace=None,                 # WB / Ozon; Yandex gated impossible
        required_scope="promotions",
        validate=_validate_stop_auto_promotion,
        dispatch=_dispatch_stop_auto_promotion,
        reversible=True,
        reverter=_revert_stop_auto_promotion,
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
