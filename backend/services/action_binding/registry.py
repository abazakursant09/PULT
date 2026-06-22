"""
Action Binding registry (Action Catalog Expansion A2) — the contract that says,
per engine signal type, whether a REAL catalog action can be bound and how its
payload would be sourced. Pure declaration: no execution, no payload building, no
promotion, no fabricated action_key.

A binding is `bound` ONLY when:
  * the action_key is a real action in action_catalog.known_actions(), AND
  * its payload is derivable from existing context (sku → listing.external_id),
    needing NO generated content / text.
Otherwise the signal stays advice_only with an explicit binding_status + reason:
  no_catalog_action     — no catalog action fits this signal
  payload_not_derivable — a catalog action exists but needs content/text we cannot
                          derive (SEO update_card, Review reply text, campaign_id)
  capability_missing    — reserved (no required scope satisfiable) — unused in A2

safety_class (Human-Control doctrine):
  auto_forbidden  — must never be automated (legal)
  manual_only     — negative reviews — manual action only, never auto
  manual_approval — executable, but only with explicit seller approval

required_capability mirrors ActionSpec.required_scope for bound bindings.

Decision Spine executable contract
----------------------------------
An action enters the Decision Spine (becomes executable through the bound apply
path, services/action_binding/execution_bridge.py) ONLY when ALL of:
  1. payload is fully derivable from existing facts — no generated content/text
     (enforced here as `bound`, and again by payload_builder at apply time);
  2. marketplace capability exists for (action, marketplace)
     (decision_bridge.capability_supported, re-checked at the bridge);
  3. safety_class permits execution — execution_bridge accepts ONLY
     `manual_approval`; `manual_only` and `auto_forbidden` are NOT executable
     through execution_bridge (rejected with safety_not_manual_approval);
  4. effect is reported honestly afterwards — decision_outcome/effect_measurement
     records a real band when an observed reader exists, else `not_evaluated`;
     a value is never fabricated.

Deliberately NOT entry gates:
  * reversibility — a RISK ATTRIBUTE (ActionSpec.reversible), not a binding gate.
    No code path reads it to decide bindability; it drives the revert/undo helper
    only. Forward rule (locked by a guard test, no auto tier exists today): a
    `reversible == False` action must never be assigned an auto-permitting
    execution class — keep it at `manual_approval` (per-instance confirm) or
    stricter.
  * measurability — NOT required to enter. The system intentionally binds
    actions whose effect may be currently uncloseable and reports `not_evaluated`
    honestly (No Fake Impact). `not_evaluated` is a VALID outcome, never a failure
    and never "no effect".

Today the only bound/executable set is the six advertising types → stop_auto_promotion
(reversible, WB/Ozon-capable, manual_approval, measured on net_profit).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from services.marketplace import action_catalog
# NOTE: decision_outcome.registry is imported LAZILY (inside _all) so that
# decision_outcome.registry can import binding_for() from here without a cycle.

# binding_status
BOUND = "bound"
NO_CATALOG_ACTION = "no_catalog_action"
PAYLOAD_NOT_DERIVABLE = "payload_not_derivable"
CAPABILITY_MISSING = "capability_missing"

# safety_class
AUTO_FORBIDDEN = "auto_forbidden"
MANUAL_ONLY = "manual_only"
MANUAL_APPROVAL = "manual_approval"

# advertising signal types whose recommended action is "stop the auto-promotion"
# and whose payload (offer_id) is derivable from sku → listing.external_id.
# ad_on_bad_listing joins the family (Action Catalog Expansion v2, P0): an ad on a
# weak listing wastes spend, so the conservative, offer_id-level, reversible action
# is the same "stop the ad" — no content generation needed (improving the listing
# itself stays a separate, advisory path).
_BOUND_ADV = frozenset({
    "ad_destroying_profit", "ad_spend_without_sales", "ad_on_unprofitable_product",
    "ad_on_low_stock", "ad_on_oos_risk", "ad_on_bad_listing",
})
# negative reviews — manual_only is mandatory (Negative-Review doctrine).
_NEGATIVE_REVIEW = frozenset({"unanswered_negative_review", "complaint_detected"})

# stop_auto_promotion needs only offer_id (verified against the catalog validator);
# derivable without any generated content. enabled=false means "stop participation".
_STOP_PAYLOAD_RULE: Mapping[str, str] = {
    "offer_id": "resolve: sku -> listing.external_id",
    "marketplace": "signal.marketplace",
    "enabled": "const:false",
}


@dataclass(frozen=True)
class ActionBinding:
    signal_type: str                       # canonical engine signal_key
    contour: str
    bindable: bool
    action_key: Optional[str]              # ONLY from action_catalog.known_actions(); else None
    payload_rule: Optional[Mapping[str, str]]
    required_capability: Optional[str]     # ActionSpec.required_scope when bound
    safety_class: str
    binding_status: str
    reason: Optional[str]


def _decide(contour: str, itype: str, signal_key: str) -> ActionBinding:
    # ── advertising ──────────────────────────────────────────────────────────
    if contour == "advertising":
        if itype in _BOUND_ADV:
            ak = "stop_auto_promotion"
            return ActionBinding(
                signal_key, contour, True, ak, _STOP_PAYLOAD_RULE,
                action_catalog.get(ak).required_scope, MANUAL_APPROVAL, BOUND, None)
        # any future advertising type without a derivable stop payload would fall
        # here (improve-listing == update_card needs generated content)
        return ActionBinding(
            signal_key, contour, False, None, None, None, MANUAL_APPROVAL,
            PAYLOAD_NOT_DERIVABLE, "update_card requires content generation")

    # ── seo → update_card always needs new content ───────────────────────────
    if contour == "seo":
        return ActionBinding(
            signal_key, contour, False, None, None, None, MANUAL_APPROVAL,
            PAYLOAD_NOT_DERIVABLE, "update_card requires content generation")

    # ── review → publish_review_response needs reply text; negatives manual_only ─
    if contour == "review":
        safety = MANUAL_ONLY if itype in _NEGATIVE_REVIEW else MANUAL_APPROVAL
        if itype == "already_answered":
            return ActionBinding(signal_key, contour, False, None, None, None, safety,
                                 NO_CATALOG_ACTION, "status only — no action")
        return ActionBinding(signal_key, contour, False, None, None, None, safety,
                             PAYLOAD_NOT_DERIVABLE, "publish_review_response requires reply text")

    # ── growth → no catalog action for these opportunities ───────────────────
    if contour == "growth":
        return ActionBinding(signal_key, contour, False, None, None, None, MANUAL_APPROVAL,
                             NO_CATALOG_ACTION, "no catalog action for this opportunity")

    # ── legal → advisory only, never automatable ─────────────────────────────
    return ActionBinding(signal_key, contour, False, None, None, None, AUTO_FORBIDDEN,
                         NO_CATALOG_ACTION, "advisory only — no executor action")


def binding_for(contour: str, insight_type: str, signal_key: str) -> ActionBinding:
    """Pure binding decision for one signal type. No decision_outcome import →
    safe to call from decision_outcome.registry during its own build."""
    return _decide(contour, insight_type, signal_key)


_CACHE: Optional[Tuple[ActionBinding, ...]] = None


def _all() -> Tuple[ActionBinding, ...]:
    """Full 35-type registry, built lazily over the canonical insight types. The
    decision_outcome import is deferred to first access (after that module is fully
    imported) so there is no import cycle."""
    global _CACHE
    if _CACHE is None:
        from services.decision_outcome.registry import CANONICAL_INSIGHT_TYPES
        _CACHE = tuple(_decide(c.contour, c.insight_type, c.signal_key)
                       for c in CANONICAL_INSIGHT_TYPES)
    return _CACHE


def bound_signal_types() -> Tuple[str, ...]:
    return tuple(b.signal_type for b in _all() if b.bindable)


def advice_only_signal_types() -> Tuple[str, ...]:
    return tuple(b.signal_type for b in _all() if not b.bindable)


def __getattr__(name: str):
    # PEP 562 lazy module attributes — keeps ACTION_BINDINGS / BY_SIGNAL_TYPE as the
    # public API while deferring the decision_outcome import.
    if name == "ACTION_BINDINGS":
        return _all()
    if name == "BY_SIGNAL_TYPE":
        return {b.signal_type: b for b in _all()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
