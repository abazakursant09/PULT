"""
Operations Auto-Promotion Margin Drain — Slice 2 E2E (full execution loop):

  OperationsSignal(operations_auto_promo_margin_drain)
    → Snapshot → Candidate → Decision(stop_auto_promotion)
    → Apply (set_auto_promotion enabled=false)
    → Measurement open (net_profit) → close → Effect band → Learning OS → Feed.

Ozon-only, observed-only. The single functional change this slice depends on is
registering "operations" in effect_measurement._MODELS (so the metric_key resolves
to net_profit instead of no_metric). Everything else rides the generic spine.
No forecast / AI / competitor / compute_recommendation / fabricated payload.
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
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.decision import Decision
from models.operations_signal import OperationsSignal
from models.engine_effect_observation import EngineEffectObservation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.ozon_client import ozon_client
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, IMPROVED, WORSENED, NOT_EVALUATED,
)
from services.decision_apply_ux.preview import build_apply_preview
from services.action_binding.execution_bridge import execute_bound_decision
from services.learning_os.registry import get_action_learning_summary

T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)
SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, sku=SKU, baseline_net_profit=-200.0):
    """Ozon cabinet (promotions scope) + listing identity + the OBSERVED operations
    signal (Ozon, in auto-promotion, net_profit<0) + a thin baseline finance row."""
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=sku))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                 status="connected", scopes=["promotions"], ozon_client_id="cid")
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="promotions",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    # real producer — proves the Signal step (observed gate: ozon + auto-promo + loss)
    await build_operations_signal(db, user_id=uid, marketplace="ozon", sku=sku,
                                  net_profit=-100.0, in_auto_promotion=True)
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                              date="2026-06-01", sku=sku, revenue=10000.0,
                              net_profit=baseline_net_profit))
    await db.commit()


def _patch_executor(monkeypatch, calls):
    async def fake(*, token, client_id, offer_id, enabled):
        calls.append((offer_id, enabled)); return {"requestId": "rq"}
    monkeypatch.setattr(ozon_client, "set_auto_promotion", fake)


async def _fin_after(db, uid, *, sku, net_profit):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                              date="2026-06-20", sku=sku, revenue=10000.0, net_profit=net_profit))
    await db.commit()


async def _promote(db, uid):
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


async def _loop_until_apply(monkeypatch, db, uid, calls):
    """Signal already seeded → promote → bridge → preview → apply."""
    await _promote(db, uid)
    decision = (await db.execute(select(Decision))).scalars().one()
    assert decision.action_key == "stop_auto_promotion"
    assert BY_SIGNAL_KEY[SIGNAL_KEY].default_metric_key == "net_profit"
    assert BY_SIGNAL_TYPE[SIGNAL_KEY].safety_class == "manual_approval"

    p = await build_apply_preview(db, user_id=uid, decision_id=decision.id,
                                  marketplace="ozon", sku=SKU)
    assert p.applyable is True and p.action_key == "stop_auto_promotion"
    assert p.payload == {"offer_id": SKU}
    assert calls == []                         # dry-run preview: no marketplace call

    res = await execute_bound_decision(db, user_id=uid, decision_id=decision.id,
                                       marketplace="ozon", sku=SKU, dry_run=False)
    assert res.ok and res.status == "success"
    assert calls == [(SKU, False)]             # stop → enabled=false, exactly once
    return decision


# ── (1) full loop → improved ─────────────────────────────────────────────────

def test_ozon_full_loop_improved(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _loop_until_apply(monkeypatch, db, uid, calls)

        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.metric_key == "net_profit"          # the _MODELS registration works
        assert json.loads(obs.evidence)["baseline"] == -200.0

        await _fin_after(db, uid, sku=SKU, net_profit=500.0)   # loss → profit
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.effect_band == IMPROVED

        summ = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                                 action_key="stop_auto_promotion")
        assert summ.improved_count == 1 and summ.marketplace == "ozon"
        assert summ.metric_key == "net_profit"
    _run(go())


# ── (2) worse after → worsened ───────────────────────────────────────────────

def test_ozon_full_loop_worsened(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _loop_until_apply(monkeypatch, db, uid, calls)

        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await _fin_after(db, uid, sku=SKU, net_profit=-500.0)   # deeper loss
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.effect_band == WORSENED
    _run(go())


# ── (3) no after finance → not_evaluated, never a fabricated number ───────────

def test_no_after_finance_not_evaluated(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _loop_until_apply(monkeypatch, db, uid, calls)

        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()   # no after row
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.effect_band == NOT_EVALUATED
        assert "after" not in json.loads(obs.evidence)        # no fabricated value
        summ = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                                 action_key="stop_auto_promotion")
        assert summ.not_evaluated_count == 1 and summ.improved_count == 0
    _run(go())


# ── (4) marketplace isolation — ozon outcome never leaks to wb ────────────────

def test_learning_marketplace_isolation(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _loop_until_apply(monkeypatch, db, uid, calls)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await _fin_after(db, uid, sku=SKU, net_profit=500.0)
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()

        wb = await get_action_learning_summary(db, user_id=uid, marketplace="wb",
                                               action_key="stop_auto_promotion")
        assert wb is None or wb.total_count == 0
    _run(go())
