"""
Bound Decision Execution Bridge (Action Catalog Expansion A5) — connect a derivable
payload to the EXISTING sanctioned apply path. It builds nothing new: it validates
the binding, builds the payload (A4), and hands off to services.decision_apply.
apply_decision — the single entry point that owns the executor, the capability
gate, the guard, the ExecutionLog and idempotency.

It NEVER calls a marketplace directly, NEVER bypasses guard/capability, NEVER
auto-executes (caller chooses dry_run), NEVER creates measurement / effect
observation here, NEVER touches Review/SEO/Growth/Legal signals, NEVER writes a new
executor. Defaults to dry_run=True.

Flow:
  Decision (scoped) → EngineSignalDecisionLink (by decision_id) → signal_type from
  insight_key → ACTION_BINDINGS checks (bindable + safety_class==manual_approval +
  action_key matches Decision.action_key) → capability pre-gate (same source of
  truth as the promotion bridge) → build_action_payload → apply_decision(overrides=
  payload, dry_run, idempotency_key, measure=False).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink

from services.decision_apply import apply_decision
from services.decision_outcome.decision_bridge import capability_supported
from services.action_binding.registry import (
    BY_SIGNAL_TYPE, BOUND, MANUAL_APPROVAL, binding_for_action,
)
from services.action_binding.payload_builder import build_action_payload

NOT_EXECUTED = "not_executed"

# The executor identifies a connection (MarketplaceConnection.marketplace) and
# routes a dispatch (action_catalog._dispatch_*) by the FULL marketplace name
# ("wildberries" / "ozon"), never the short code. Hand the executor that form —
# normalizing to the short "wb"/"ozon" code here made the connection lookup and
# the dispatch fall through to "unsupported marketplace", so a real apply could
# never succeed for stop_auto_promotion / reduce_discount. Unknown inputs pass
# through unchanged (the executor then fails closed on connection/capability).
_EXECUTOR_MARKETPLACE = {
    "wb": "wildberries", "wildberries": "wildberries", "вб": "wildberries",
    "ozon": "ozon", "озон": "ozon",
    "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex",
}


def _executor_marketplace(marketplace: Optional[str]) -> Optional[str]:
    if not marketplace:
        return marketplace
    return _EXECUTOR_MARKETPLACE.get(marketplace.lower(), marketplace)


@dataclass
class BoundExecutionResult:
    ok: bool
    decision_id: str
    action_key: Optional[str]
    payload: Optional[Mapping[str, object]]
    execution_log_id: Optional[str]
    status: str
    reason: Optional[str]


def _reject(decision_id, action_key, reason) -> BoundExecutionResult:
    return BoundExecutionResult(False, decision_id, action_key, None, None, NOT_EXECUTED, reason)


async def execute_bound_decision(
    db: AsyncSession, *, user_id: str, decision_id: str, marketplace: str,
    sku: Optional[str], dry_run: bool = True, idempotency_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> BoundExecutionResult:
    """Apply a bound advertising Decision through the existing apply path. dry_run
    by default — no auto-execution."""
    # 1) Decision (scoped to user)
    decision = (await db.execute(select(Decision).where(
        Decision.id == decision_id, Decision.user_id == user_id))).scalar_one_or_none()
    if decision is None:
        return _reject(decision_id, None, "decision_not_found")

    # 2) the engine link that promoted it
    link = (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.decision_id == decision_id,
        EngineSignalDecisionLink.user_id == user_id))).scalars().first()
    if link is None:
        return _reject(decision_id, decision.action_key, "link_not_found")

    # 3) signal_type from the canonical insight_key (<signal_type>:<mp>:<sku>)
    signal_type = (link.insight_key or "").split(":", 1)[0] or None

    # 4) binding checks — resolve the binding for THIS Decision's action_key (the
    #    signal may carry several alternative levers; pick the matching one).
    b = binding_for_action(signal_type, decision.action_key)
    if b is None or not b.bindable or b.binding_status != BOUND or not b.action_key:
        primary = BY_SIGNAL_TYPE.get(signal_type)
        if primary is None or not primary.bindable:
            return _reject(decision_id, decision.action_key, "not_bindable")
        return _reject(decision_id, decision.action_key, "action_key_mismatch")
    if b.safety_class != MANUAL_APPROVAL:
        return _reject(decision_id, decision.action_key, "safety_not_manual_approval")

    # 5) capability pre-gate (same source of truth as the promotion bridge) — never
    #    bypassing the executor's own gate, just an honest early skip.
    if not capability_supported(decision.action_key, marketplace):
        return _reject(decision_id, decision.action_key, "unsupported_capability")

    # 6) build the payload for THIS lever (derivable data only)
    pr = await build_action_payload(db, user_id=user_id, signal_type=signal_type,
                                    marketplace=marketplace, sku=sku,
                                    action_key=decision.action_key)
    if not pr.ok or not pr.payload:
        return _reject(decision_id, decision.action_key, pr.reason or "payload_not_derivable")

    # 7) hand off to the sanctioned apply path. overrides = payload + marketplace
    #    (routing context); apply_decision owns executor/guard/capability/log/idemp.
    overrides = {**dict(pr.payload), "marketplace": _executor_marketplace(marketplace)}
    res = await apply_decision(
        db=db, user_id=user_id, decision_id=decision_id, overrides=overrides,
        idempotency_key=idempotency_key, dry_run=dry_run, measure=False, now=now)

    return BoundExecutionResult(
        ok=res.ok, decision_id=decision_id, action_key=decision.action_key,
        payload=pr.payload, execution_log_id=res.execution_log_id,
        status=res.status, reason=res.reason)
