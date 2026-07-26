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

from services.marketplace import credential_vault, executor
from services.marketplace.ozon_client import ozon_client
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import open_effect_measurement
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


async def _promote(db, uid):
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


# 2.1A: stop_auto_promotion is contained. The operations auto-promo margin-drain signal stays a
# DIAGNOSTIC — it never promotes to an executable Decision, its execution fail-closes, and no
# marketplace call is ever made. The generic measure→learn machinery (improved/worsened/
# not_evaluated + learning buckets) is proven for a still-executable lever in
# test_decision_outcome_measurement.py (pricing_price_below_floor → set_price, net_profit) and the
# real-execution path in test_decision_apply_execution_loop.py (reduce_discount).


# ── (1) the operations signal is a diagnostic — never an executable Decision ──

def test_signal_diagnostic_not_promoted(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _promote(db, uid)
        assert BY_SIGNAL_KEY[SIGNAL_KEY].default_metric_key == "net_profit"
        assert BY_SIGNAL_TYPE[SIGNAL_KEY].safety_class == "manual_approval"
        # the contained action never promotes to a Decision (no false button)
        assert (await db.execute(select(Decision))).scalars().all() == []
        sig = (await db.execute(select(OperationsSignal))).scalars().one()
        assert sig.status == "active"            # stays a diagnostic
        assert calls == []                       # 0 marketplace calls
    _run(go())


# ── (2) direct execution of the contained action fail-closes, 0 calls ─────────

def test_direct_execute_contained(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        res = await executor.execute(db=db, user_id=uid, action_type="stop_auto_promotion",
                                     payload={"marketplace": "ozon", "offer_id": SKU})
        assert res.status == "rejected" and not res.ok        # server-side fail-closed
        assert calls == []                                    # 0 marketplace calls
    _run(go())


# ── (3) nothing executed → no measurement for the contained action ────────────

def test_no_measurement_without_execution(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _promote(db, uid)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        assert (await db.execute(select(EngineEffectObservation))).scalars().all() == []
        assert calls == []
    _run(go())


# ── (4) no learning bucket forms for the contained action ─────────────────────

def test_no_learning_bucket_for_contained(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); calls = []
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        await _promote(db, uid)
        summ = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                                 action_key="stop_auto_promotion")
        assert summ is None or summ.total_count == 0
        assert calls == []
    _run(go())
