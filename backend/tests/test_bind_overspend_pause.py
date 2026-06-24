"""
A2.2-bind — overspend advertising signals → ad_set_state (campaign pause).

The three DIRECT overspend types bind to ad_set_state; payload campaign_id comes
ONLY from the campaign_identity resolver on a single unambiguous match (else honest
payload_not_derivable with the exact reason). Measured on ad_cost_ratio. WB + Ozon
only; Yandex/Megamarket stay unsupported. No guessed campaign_id, no auto-pick, no
auto-apply, no schema change.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest

from database import Base
import models  # registers tables
from models.decision import Decision
from models.advertising_signal import AdvertisingSignal
from models.marketplace_connection import MarketplaceConnection

from services.action_binding import payload_builder as pb
from services.action_binding.payload_builder import build_action_payload
from services.action_binding.registry import BY_SIGNAL_TYPE, BOUND
from services.marketplace.campaign_identity import CampaignIdentity, CampaignUnavailable
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.marketplace.action_metric_binding import target_metric

OVERSPEND = ("ad_destroying_profit", "ad_spend_without_sales", "ad_on_unprofitable_product")
NOT_OVERSPEND = ("ad_on_low_stock", "ad_on_oos_risk", "ad_on_bad_listing")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _patch_identity(monkeypatch, result):
    async def fake(marketplace, **kw):
        return result
    monkeypatch.setattr(pb, "resolve_campaign_identity", fake)


def _identity(mp="wb", cid=555):
    return CampaignIdentity(marketplace=mp, campaign_id=cid, campaign_type="SKU",
                            campaign_state="running", source=f"{mp}_api")


# ── (1) each overspend signal binds to ad_set_state ──────────────────────────

def test_overspend_signals_bind_ad_set_state():
    for it in OVERSPEND:
        b = BY_SIGNAL_TYPE[f"adv_{it}"]
        assert b.bindable and b.binding_status == BOUND
        assert b.action_key == "ad_set_state"
        # required_capability mirrors the ActionSpec scope; campaign_control is the
        # executor capability that gates this action at execution.
        assert b.required_capability == "advert"
        from services.marketplace.executor import capability_for_action
        assert capability_for_action("ad_set_state") == "campaign_control"
        assert b.safety_class == "manual_approval"


# ── (2) stock/listing signals do NOT bind ad_set_state ───────────────────────

def test_indirect_signals_not_ad_set_state():
    for it in NOT_OVERSPEND:
        b = BY_SIGNAL_TYPE[f"adv_{it}"]
        assert b.action_key == "stop_auto_promotion"   # unchanged
        assert b.action_key != "ad_set_state"


# ── (3)(4) WB / Ozon payload success with a single campaign ───────────────────

@pytest.mark.parametrize("mp", ["wildberries", "ozon"])
def test_payload_success_single_campaign(monkeypatch, mp):
    _patch_identity(monkeypatch, _identity(mp="wb" if mp == "wildberries" else "ozon", cid=777))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="adv_ad_destroying_profit",
                                         marketplace=mp, sku="SKU1")
        assert res.ok and res.action_key == "ad_set_state"
        assert res.payload == {"campaign_id": 777, "action": "pause"}
        assert set(res.payload) == {"campaign_id", "action"}   # only allowed fields
    _run(go())


# ── (5) zero campaign → payload_not_derivable (exact reason) ─────────────────

def test_zero_campaign_not_derivable(monkeypatch):
    _patch_identity(monkeypatch, CampaignUnavailable("wb", "no_campaign_for_listing"))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="adv_ad_spend_without_sales",
                                         marketplace="wildberries", sku="SKU1")
        assert res.ok is False and res.payload is None
        assert res.reason == "no_campaign_for_listing"
    _run(go())


# ── (6) multiple campaigns → ambiguous_multiple (never auto-pick) ────────────

def test_multiple_campaigns_ambiguous(monkeypatch):
    _patch_identity(monkeypatch, CampaignUnavailable("ozon", "ambiguous_multiple"))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="adv_ad_on_unprofitable_product",
                                         marketplace="ozon", sku="SKU1")
        assert res.ok is False and res.reason == "ambiguous_multiple"
    _run(go())


# ── (7) missing credentials → no_scope ───────────────────────────────────────

def test_missing_credential_no_scope(monkeypatch):
    _patch_identity(monkeypatch, CampaignUnavailable("ozon", "no_scope"))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="adv_ad_destroying_profit",
                                         marketplace="ozon", sku="SKU1")
        assert res.ok is False and res.reason == "no_scope"
    _run(go())


# ── (8) Yandex / Megamarket unsupported (never calls resolver) ───────────────

@pytest.mark.parametrize("mp", ["yandex", "megamarket"])
def test_unsupported_marketplaces(monkeypatch, mp):
    def boom(*a, **k):
        raise AssertionError("unsupported marketplace must not reach the resolver")
    monkeypatch.setattr(pb, "resolve_campaign_identity", boom)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="adv_ad_destroying_profit",
                                         marketplace=mp, sku="SKU1")
        assert res.ok is False and res.action_key == "ad_set_state"
        assert res.reason == "no_adapter"
    _run(go())


# ── (9) metric_key = ad_cost_ratio for overspend ─────────────────────────────

def test_metric_routes_to_ad_cost_ratio():
    for it in OVERSPEND:
        assert BY_SIGNAL_KEY[f"adv_{it}"].default_metric_key == "ad_cost_ratio"
    # the action→metric binding agrees (ad_set_state → ДРР)
    assert target_metric("ad_set_state") == "ad_cost_ratio"
    # untouched advertising types keep the contour default
    assert BY_SIGNAL_KEY["adv_ad_on_low_stock"].default_metric_key == "ad_profit_impact"


# ── (10) decision bridge promotes overspend as ad_set_state (capability-gated) ─

def test_bridge_promotes_overspend_as_ad_set_state():
    from services.decision_outcome.promotion import promote_eligible_candidates
    from services.decision_outcome.decision_bridge import (
        bridge_links_to_decisions, PROMOTED, SKIPPED_NO_CAPABILITY)

    async def _adv(db, uid, *, mp, sku):
        db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
               insight_key=f"adv_ad_destroying_profit:{mp}:{sku}", marketplace=mp, sku=sku,
               status="active", what="x", why="y", expected_effect="z", what_to_do="w",
               priority_level="high"))
        await db.commit()

    async def go():
        # WB → promoted as ad_set_state
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, mp="wildberries", sku="SKU1")
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 1 and res.items[0].outcome == PROMOTED
        d = (await db.execute(select(Decision))).scalars().one()
        assert d.action_key == "ad_set_state"

        # Yandex → capability-blocked (honest, no promotion)
        db2 = await _engine(); uid2 = str(uuid.uuid4())
        await _adv(db2, uid2, mp="yandex", sku="SKU9")
        await promote_eligible_candidates(db2, user_id=uid2); await db2.commit()
        res2 = await bridge_links_to_decisions(db2, user_id=uid2); await db2.commit()
        assert res2.promoted == 0 and res2.items[0].outcome == SKIPPED_NO_CAPABILITY
        assert (await db2.execute(select(Decision))).scalars().first() is None
    _run(go())


# ── (11)(12) apply preview applyable only with a valid payload; no auto-apply ─

def test_preview_applyable_only_with_payload(monkeypatch):
    from services.decision_apply_ux.preview import build_apply_preview, PAYLOAD_NOT_DERIVABLE
    from models.engine_signal_decision_link import EngineSignalDecisionLink

    async def _seed(db, uid):
        did = str(uuid.uuid4())
        ikey = "adv_ad_destroying_profit:wildberries:SKU1"
        db.add(Decision(id=did, user_id=uid, problem="adv", action_key="ad_set_state",
                        insight_key=ikey, status="open"))
        db.add(MarketplaceConnection(user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["advert"]))
        db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
               signal_table="advertising_signal", signal_id="s1", insight_key=ikey,
               action_key="ad_set_state", decision_id=did, link_status="promoted",
               marketplace="wildberries", sku="SKU1"))
        db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
               insight_key=ikey, marketplace="wildberries", sku="SKU1",
               status="promoted_to_decision"))
        await db.commit()
        return did

    async def go():
        # valid single campaign → applyable
        _patch_identity(monkeypatch, _identity(cid=900))
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wildberries", sku="SKU1")
        assert p.applyable is True and p.action_key == "ad_set_state"
        assert p.payload == {"campaign_id": 900, "action": "pause"}
        assert p.safety_class == "manual_approval"   # manual approval required (no auto-apply)

        # ambiguous → not applyable, honest payload_not_derivable
        _patch_identity(monkeypatch, CampaignUnavailable("wb", "ambiguous_multiple"))
        db2 = await _engine(); uid2 = str(uuid.uuid4())
        did2 = await _seed(db2, uid2)
        p2 = await build_apply_preview(db2, user_id=uid2, decision_id=did2,
                                       marketplace="wildberries", sku="SKU1")
        assert p2.applyable is False and p2.payload is None
        assert p2.payload_status == PAYLOAD_NOT_DERIVABLE and p2.reason == "ambiguous_multiple"
    _run(go())


# ── (13) no new schema: bind adds no ORM model/table ─────────────────────────

def test_no_new_schema():
    import importlib
    for mod_name in ("services.action_binding.payload_builder",
                     "services.action_binding.registry"):
        mod = importlib.import_module(mod_name)
        # a NEW table would be a Base subclass DEFINED in this module (not imported)
        defined_models = [v for v in vars(mod).values()
                          if isinstance(v, type) and issubclass(v, Base)
                          and v is not Base and getattr(v, "__module__", "") == mod_name]
        assert defined_models == []
