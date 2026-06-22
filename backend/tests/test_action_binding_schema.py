"""
Action Catalog Expansion A2 — registry + schema tests.

ACTION_BINDINGS covers all 35 engine signal types; a bound action_key is a real
catalog action with a derivable payload; everything else is advice_only with an
explicit reason. Negative reviews are manual_only. capability = ActionSpec scope.
action_binding_audit is append-only (no updated_at), no score/forecast/priority.
"""
import asyncio
import uuid

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.action_binding_audit import ActionBindingAudit

from services.marketplace import action_catalog
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.registry import (
    ACTION_BINDINGS, BY_SIGNAL_TYPE, bound_signal_types, advice_only_signal_types,
    BOUND, NO_CATALOG_ACTION, PAYLOAD_NOT_DERIVABLE,
    MANUAL_ONLY, MANUAL_APPROVAL, AUTO_FORBIDDEN,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── 1. registry covers all 35 signal types ──────────────────────────────────

def test_registry_covers_all_signal_types():
    assert set(BY_SIGNAL_TYPE.keys()) == set(BY_SIGNAL_KEY.keys())
    assert len(ACTION_BINDINGS) == 35


# ── 2. bound action_key is a real catalog action ────────────────────────────

def test_bound_action_in_catalog():
    known = set(action_catalog.known_actions())
    for b in ACTION_BINDINGS:
        if b.bindable:
            assert b.action_key in known, b.signal_type
            assert b.binding_status == BOUND and b.payload_rule and b.required_capability
        else:
            assert b.action_key is None and b.binding_status != BOUND


# ── 3. exactly the six advertising types are bound (v2 P0: + bad_listing) ─────

def test_only_six_advertising_bound():
    bound = set(bound_signal_types())
    assert bound == {
        "adv_ad_destroying_profit", "adv_ad_spend_without_sales",
        "adv_ad_on_unprofitable_product", "adv_ad_on_low_stock", "adv_ad_on_oos_risk",
        "adv_ad_on_bad_listing",
    }
    assert len(advice_only_signal_types()) == 29
    for st in bound:
        assert BY_SIGNAL_TYPE[st].action_key == "stop_auto_promotion"


# ── 4. payload_not_derivable correctly set (SEO + review-text) ───────────────

def test_payload_not_derivable():
    seo = [b for b in ACTION_BINDINGS if b.contour == "seo"]
    assert seo and all(b.binding_status == PAYLOAD_NOT_DERIVABLE for b in seo)
    # v2 P0: adv_ad_on_bad_listing is now BOUND (no longer payload_not_derivable)
    assert BY_SIGNAL_TYPE["adv_ad_on_bad_listing"].binding_status == BOUND
    # review text-based → payload_not_derivable; already_answered → no_catalog_action
    assert BY_SIGNAL_TYPE["rev_unanswered_negative_review"].binding_status == PAYLOAD_NOT_DERIVABLE
    assert BY_SIGNAL_TYPE["rev_already_answered"].binding_status == NO_CATALOG_ACTION


# ── 5. negative reviews are manual_only; legal auto_forbidden ────────────────

def test_safety_classes():
    assert BY_SIGNAL_TYPE["rev_unanswered_negative_review"].safety_class == MANUAL_ONLY
    assert BY_SIGNAL_TYPE["rev_complaint_detected"].safety_class == MANUAL_ONLY
    # non-negative review → not manual_only (and never bound)
    assert BY_SIGNAL_TYPE["rev_safe_review_can_reply"].safety_class == MANUAL_APPROVAL
    # legal is advisory, never automatable
    for b in ACTION_BINDINGS:
        if b.contour == "legal":
            assert b.safety_class == AUTO_FORBIDDEN and not b.bindable
    # no bound binding is ever auto (none are auto_forbidden-bound either)
    for b in ACTION_BINDINGS:
        if b.bindable:
            assert b.safety_class in (MANUAL_APPROVAL, MANUAL_ONLY)


# ── 6. required_capability == ActionSpec.required_scope ──────────────────────

def test_capability_matches_action_spec():
    for b in ACTION_BINDINGS:
        if b.bindable:
            assert b.required_capability == action_catalog.get(b.action_key).required_scope
            assert b.required_capability == "promotions"   # stop_auto_promotion scope


# ── 7. growth + legal advice_only with reasons ───────────────────────────────

def test_growth_legal_advice_only():
    for b in ACTION_BINDINGS:
        if b.contour in ("growth", "legal"):
            assert not b.bindable and b.binding_status == NO_CATALOG_ACTION and b.reason


# ── 8. append-only audit (no updated_at) + roundtrip + no analytics cols ─────

def test_audit_append_only_roundtrip():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    c = cols(ActionBindingAudit)
    assert "updated_at" not in c
    for bad in ("score", "forecast", "priority", "rank", "ranking", "weight", "pnl"):
        assert bad not in c, bad

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(ActionBindingAudit(user_id=uid, signal_type="adv_ad_destroying_profit",
               action_key="stop_auto_promotion", binding_status="bound",
               reason=None, marketplace="wildberries"))
        db.add(ActionBindingAudit(user_id=uid, signal_type="seo_title_too_short",
               action_key=None, binding_status="payload_not_derivable",
               reason="update_card requires content generation"))
        await db.commit()
        rows = (await db.execute(select(ActionBindingAudit))).scalars().all()
        assert len(rows) == 2
        bound = next(r for r in rows if r.binding_status == "bound")
        assert bound.action_key == "stop_auto_promotion"
    _run(go())
