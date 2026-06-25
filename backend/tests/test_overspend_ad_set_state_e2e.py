"""
A2.3 — end-to-end verification of the advertising overspend loop:

  Signal → Decision → Apply (pause) → Measurement open → close (ad_cost_ratio)
  → Effect band → Learning OS aggregation.

Proves the wired pieces actually connect, for WB and Ozon separately, with
marketplace isolation. Overspend signal → ad_set_state; campaign_id derived ONLY
through the campaign_identity resolver (single match); measured on ad_cost_ratio
(NOT ad_profit_impact); manual approval; no auto-apply; honest not_evaluated when
finance is absent. No new action / binding / schema.
"""
import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.wb_client import wb_client
from services.marketplace.ozon_client import ozon_client
from services.marketplace import ozon_performance_auth
from services.marketplace.campaign_identity import CampaignIdentity
from services.action_binding import payload_builder as pb
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, IMPROVED, NOT_EVALUATED,
)
from services.decision_apply_ux.preview import build_apply_preview
from services.action_binding.execution_bridge import execute_bound_decision
from services.learning_os.registry import get_action_learning_summary

# baseline window @ T0 (high ДРР), close @ T1 (low ДРР), 20 days apart so the
# baseline rows fall OUTSIDE the close window — close sees only post-action finance.
T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)
CID = 12345


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_signal(db, uid, *, mp, sku, itype="ad_destroying_profit"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key=f"adv_{itype}", problem_type=itype,
           insight_key=f"adv_{itype}:{mp}:{sku}", marketplace=mp, sku=sku, status="active",
           what="x", why="y", expected_effect="z", what_to_do="w", priority_level="high"))
    await db.commit()


async def _seed_connection(db, uid, *, mp):
    if mp == "wb":
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["advert"])
        db.add(conn); await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="advert",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
    else:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                     status="connected", scopes=["advert_performance"],
                                     ozon_client_id="cid")
        db.add(conn); await db.flush()
    await db.commit()


async def _fin(db, uid, *, mp, sku, date, ad_spend, revenue):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, revenue=revenue, ad_spend=ad_spend))
    await db.flush()


def _patch_resolver(monkeypatch, mp):
    async def fake(marketplace, **kw):
        return CampaignIdentity(marketplace=mp, campaign_id=CID, campaign_type="SKU",
                                campaign_state="running", source=f"{mp}_api")
    monkeypatch.setattr(pb, "resolve_campaign_identity", fake)


# Patch the marketplace client method itself (monkeypatch — auto-restored, and robust
# to other tests that globally reassign these clients). The fake records the path the
# real client WOULD hit, derived from the dispatched action, so we still verify the
# start/pause → activate/deactivate (Ozon) and pause (WB) mapping end-to-end.
def _patch_wb_adapter(monkeypatch, calls):
    async def fake(*, token, campaign_id, action):
        calls.append(f"/adv/v0/{action}"); return {"requestId": "rq"}
    monkeypatch.setattr(wb_client, "set_campaign_state", fake)


def _patch_ozon_adapter(monkeypatch, calls):
    async def fake(*, token, campaign_id, action):
        calls.append(f"/api/client/campaign/{int(campaign_id)}/{action}"); return {"requestId": "rq"}
    monkeypatch.setattr(ozon_client, "set_campaign_state", fake)
    async def fake_bearer(_db, *, connection_id, **kw):
        return "B"
    monkeypatch.setattr(ozon_performance_auth, "acquire_bearer", fake_bearer)


async def _promote(db, uid):
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


# ── full loop (parametrised over WB / Ozon), proving improved + isolation ─────

async def _full_loop(monkeypatch, *, mp, adapter_calls, expect_path):
    db = await _engine(); uid = str(uuid.uuid4()); sku = "SKU1"
    await _seed_signal(db, uid, mp=mp, sku=sku)
    await _seed_connection(db, uid, mp=mp)
    _patch_resolver(monkeypatch, mp)
    if mp == "wb":
        _patch_wb_adapter(monkeypatch, adapter_calls)
    else:
        _patch_ozon_adapter(monkeypatch, adapter_calls)

    # 2) promote + bridge → Decision(ad_set_state), manual approval, metric routed
    await _promote(db, uid)
    decision = (await db.execute(select(Decision))).scalars().one()
    assert decision.action_key == "ad_set_state"
    assert BY_SIGNAL_KEY["adv_ad_destroying_profit"].default_metric_key == "ad_cost_ratio"
    assert BY_SIGNAL_TYPE["adv_ad_destroying_profit"].safety_class == "manual_approval"

    # 3) preview (dry_run) — payload resolved via campaign_identity, applyable
    p = await build_apply_preview(db, user_id=uid, decision_id=decision.id,
                                  marketplace=mp, sku=sku)
    assert p.applyable is True and p.action_key == "ad_set_state"
    assert p.payload == {"campaign_id": CID, "action": "pause"}
    # 5a) no auto-apply: the dry_run preview never reached the marketplace adapter
    assert adapter_calls == []

    # 4) real apply → the pause/deactivate path is called exactly now
    res = await execute_bound_decision(db, user_id=uid, decision_id=decision.id,
                                       marketplace=mp, sku=sku, dry_run=False)
    assert res.ok and res.status == "success"
    assert expect_path in adapter_calls

    # 5) measurement open on ad_cost_ratio (NOT ad_profit_impact)
    await _fin(db, uid, mp=mp, sku=sku, date="2026-06-01", ad_spend=4000.0, revenue=10000.0)  # ДРР 40
    await db.commit()
    await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
    obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
    assert obs.metric_key == "ad_cost_ratio" and obs.metric_key != "ad_profit_impact"
    assert json.loads(obs.evidence)["baseline"] == 40.0

    # 6) post-action finance lowers ДРР → close → improved (lower is better)
    await _fin(db, uid, mp=mp, sku=sku, date="2026-06-20", ad_spend=1000.0, revenue=10000.0)  # ДРР 10
    await db.commit()
    await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
    obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
    assert obs.effect_band == IMPROVED

    # 7) Learning OS aggregates the proven improved outcome for this marketplace
    summ = await get_action_learning_summary(db, user_id=uid, marketplace=mp,
                                             action_key="ad_set_state")
    assert summ.improved_count == 1 and summ.marketplace == mp
    return db, uid


def test_wb_full_loop_improved(monkeypatch):
    calls = []
    _run(_full_loop(monkeypatch, mp="wb", adapter_calls=calls, expect_path="/adv/v0/pause"))


def test_ozon_full_loop_improved(monkeypatch):
    calls = []
    _run(_full_loop(monkeypatch, mp="ozon", adapter_calls=calls,
                    expect_path=f"/api/client/campaign/{CID}/deactivate"))


# ── (3) unavailable metric → not_evaluated, no fake zero ─────────────────────

def test_unavailable_metric_closes_not_evaluated(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); sku = "SKU1"
        await _seed_signal(db, uid, mp="wb", sku=sku)
        _patch_resolver(monkeypatch, "wb")
        await _promote(db, uid)
        # baseline present, but NO post-action finance in the close window
        await _fin(db, uid, mp="wb", sku=sku, date="2026-06-01", ad_spend=4000.0, revenue=10000.0)
        await db.commit()
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.effect_band == NOT_EVALUATED
        ev = json.loads(obs.evidence)
        assert "after" not in ev          # never a fabricated 0
        summ = await get_action_learning_summary(db, user_id=uid, marketplace="wb",
                                                 action_key="ad_set_state")
        assert summ.not_evaluated_count == 1 and summ.improved_count == 0
    _run(go())


# ── (4) marketplace isolation in Learning OS ─────────────────────────────────

def test_learning_marketplace_isolation(monkeypatch):
    db, uid = _run(_full_loop(monkeypatch, mp="wb", adapter_calls=[], expect_path="/adv/v0/pause"))
    async def check():
        # the WB improved outcome must NOT leak into Ozon's bucket
        wb = await get_action_learning_summary(db, user_id=uid, marketplace="wb",
                                               action_key="ad_set_state")
        oz = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                               action_key="ad_set_state")
        assert wb.improved_count == 1
        # Ozon bucket is empty (None summary) — the WB outcome never leaked across.
        assert oz is None or oz.total_count == 0
    _run(check())


# ── (6) metric routing is ad_cost_ratio, not ad_profit_impact ────────────────

def test_metric_key_is_ad_cost_ratio_not_profit():
    for it in ("ad_destroying_profit", "ad_spend_without_sales", "ad_on_unprofitable_product"):
        assert BY_SIGNAL_KEY[f"adv_{it}"].default_metric_key == "ad_cost_ratio"
    # an indirect type still uses the contour default — proves the override is scoped
    assert BY_SIGNAL_KEY["adv_ad_on_low_stock"].default_metric_key == "ad_profit_impact"
