"""
Guard — ad_set_bid stays CANONICAL-UNBOUND (legacy-only execute path).

ad_set_bid is NOT dead code: it is live in the LEGACY path
(insight_decision_bridge: high_ad_spend -> ad_set_bid, exercised by
test_advertising_execute / test_insight_execute). But the CANONICAL Decision Spine
must never make it executable — its bid (cpm) has no observed-derivable payload
(Canonical Surface Doctrine). This test locks that separation so a future PR cannot
accidentally turn ad_set_bid into a canonical executable through a wrong gate.

No runtime change is required or asserted beyond the existing state.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  (registers tables)

from services.action_binding.registry import (
    BY_SIGNAL_TYPE, binding_for_action, bound_signal_types, BOUND,
)
from services.action_binding.payload_builder import build_action_payload
from services.execution_measurement_bridge import _MEASURABLE_ACTIONS
from services.marketplace.executor import capability_for_action

ACTION = "ad_set_bid"
# canonical advertising signal_keys (decision_outcome registry _TYPES["advertising"])
_ADV_SIGNALS = (
    "adv_ad_destroying_profit", "adv_ad_spend_without_sales",
    "adv_ad_on_unprofitable_product", "adv_ad_on_low_stock",
    "adv_ad_on_bad_listing", "adv_ad_on_oos_risk",
)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── (1) no canonical signal_type binds ad_set_bid ────────────────────────────

def test_no_canonical_signal_binds_ad_set_bid():
    offenders = [st for st in bound_signal_types() if BY_SIGNAL_TYPE[st].action_key == ACTION]
    assert offenders == [], f"ad_set_bid must stay canonical-unbound, bound by: {offenders}"
    # and it is nowhere in the binding registry as a bound action_key
    assert all(b.action_key != ACTION for b in BY_SIGNAL_TYPE.values())


# ── (2) binding_for_action(adv signal, ad_set_bid) is never BOUND ────────────

def test_binding_for_action_never_bound_for_ad_set_bid():
    for sig in _ADV_SIGNALS:
        b = binding_for_action(sig, ACTION)
        assert b is None or b.binding_status != BOUND, f"{sig} unexpectedly binds {ACTION}"


# ── (3) no derivable canonical payload for ad_set_bid ────────────────────────

def test_no_canonical_payload_for_ad_set_bid():
    async def go():
        db = await _db()
        for sig in _ADV_SIGNALS:
            res = await build_action_payload(
                db, user_id=str(uuid.uuid4()), signal_type=sig, marketplace="wb",
                sku="SKU1", action_key=ACTION)
            assert res.ok is False and res.payload is None
    _run(go())


# ── (4) ad_set_bid is not a canonical measurable action ──────────────────────

def test_ad_set_bid_not_measurable():
    assert ACTION not in _MEASURABLE_ACTIONS


# ── (5)(6) capability mapping stays legacy-shared (intentional, not a remap) ──

def test_capability_mapping_is_legacy_shared():
    # ad_set_bid intentionally SHARES campaign_control as a legacy-only gate; a
    # dedicated campaign_bid capability is deferred to Phase-0 (see executor.py).
    assert capability_for_action(ACTION) == "campaign_control"
    # regression: ad_set_state (the real campaign on/off action) also maps here
    assert capability_for_action("ad_set_state") == "campaign_control"
