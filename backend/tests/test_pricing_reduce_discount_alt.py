"""
reduce_discount as the first canonical alternative for pricing_negative_margin.

One business operation: lower/remove the WB discount (discount=0). Distinct lever
alongside set_price (break-even). WB-only by capability (Ozon/Yandex/Megamarket are
honest-unavailable — NEVER set_price masking). Observed payload, no forecast/AI/
competitor/compute_recommendation. Measured net_profit; Learning aggregates it under
action_key=reduce_discount.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product import Product
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.pricing_signal import PricingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.imported_finance import ImportedFinanceRow
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential

from services.marketplace import credential_vault, action_catalog
from services.marketplace.wb_client import wb_client
from services.marketplace.ozon_client import ozon_client
from services.action_binding import payload_builder as pb
from services.action_binding.payload_builder import build_action_payload
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.decision_bridge import capability_supported
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions, PROMOTED
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, IMPROVED,
)
from services.decision_apply_ux.preview import build_apply_preview
from services.action_binding.execution_bridge import execute_bound_decision
from services.learning_os.registry import get_action_learning_summary
from services.marketplace.errors import ExecutionError

SIG = "pricing_negative_margin"
RD = "reduce_discount"
T0 = __import__("datetime").datetime(2026, 6, 1)
T1 = __import__("datetime").datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, mp="wb", sku="SKU1", listing=True, finance=True):
    """mp is a canonical code (wb / ozon). Listing/product/signal/finance use the
    canonical code; the connection carries the executor label."""
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="товар", marketplace=mp, sku=sku, price=40.0))
    if listing:
        db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace=mp, external_id=sku))
    label = "wildberries" if mp == "wb" else mp
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=label,
                                 status="connected", scopes=["prices"],
                                 ozon_client_id="cid" if mp == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    ikey = f"pricing_negative_margin:{mp}:{sku}"
    db.add(PricingSignal(user_id=uid, signal_key=SIG, problem_type="negative_margin",
           insight_key=ikey, marketplace=mp, sku=sku, status="active",
           what="x", priority_level="critical", category="pricing"))
    if finance:
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                  date="2026-06-01", sku=sku, revenue=10000.0, commission=1000.0,
                                  logistics=100.0, quantity=10, net_profit=-500.0))
    await db.commit()


# ── (8) set_price stays primary; reduce_discount is the alternative ──────────

def test_negative_margin_two_levers_primary_set_price():
    e = BY_SIGNAL_KEY[SIG]
    assert e.action_key == "set_price"                    # primary unchanged
    assert e.action_keys == ("set_price", RD)
    b = BY_SIGNAL_TYPE[SIG]
    assert b.action_key == "set_price"                    # legacy single accessor unchanged


# ── (2) WB reduce_discount payload: {offer_id, discount:0} ───────────────────

def test_wb_reduce_discount_payload():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, mp="wb")
        res = await build_action_payload(db, user_id=uid, signal_type=SIG,
                                         marketplace="wb", sku="SKU1", action_key=RD)
        assert res.ok and res.action_key == RD
        assert res.payload == {"offer_id": "SKU1", "discount": 0}
        assert set(res.payload) <= {"offer_id", "discount"}
    _run(go())


def test_reduce_discount_payload_not_derivable_without_listing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, mp="wb", listing=False)
        res = await build_action_payload(db, user_id=uid, signal_type=SIG,
                                         marketplace="wb", sku="SKU1", action_key=RD)
        assert res.ok is False and res.payload is None and res.reason == "payload_not_derivable"
    _run(go())


# ── (3)(4)(5) Ozon/Yandex/Megamarket honest unavailable (NEVER set_price) ────

def test_reduce_discount_capability_wb_only():
    assert capability_supported(RD, "wildberries") is True
    assert capability_supported(RD, "ozon") is False
    assert capability_supported(RD, "yandex") is False
    assert capability_supported(RD, "megamarket") is False


def test_reduce_discount_spec_is_wb_only_and_never_set_price():
    spec = action_catalog.get(RD)
    assert spec.marketplace == "wildberries"
    import inspect
    src = inspect.getsource(action_catalog._dispatch_reduce_discount)
    # the dispatcher must call the WB discount API and NEVER ozon_client.set_price
    assert "wb_client.set_discount" in src
    assert "ozon_client" not in src and ".set_price(" not in src   # no masking


def test_ozon_reduce_discount_dispatch_rejects_not_set_price(monkeypatch):
    # if dispatch were ever reached for Ozon it must reject, never call set_price
    boom = []
    monkeypatch.setattr(ozon_client, "set_price",
                        lambda **k: boom.append(1))
    async def go():
        err = None
        try:
            await action_catalog._dispatch_reduce_discount(
                "tok", {"offer_id": "1", "discount": 0}, {"marketplace": "ozon"})
        except ExecutionError as e:
            err = e
        assert err is not None and err.code == ExecutionError.CAPABILITY_NOT_SUPPORTED
        assert boom == []                                  # set_price never called
    _run(go())


# ── (9) measure uses net_profit ──────────────────────────────────────────────

def test_reduce_discount_metric_net_profit():
    from services.marketplace.action_metric_binding import target_metric
    assert target_metric(RD) == "net_profit"
    assert BY_SIGNAL_KEY[SIG].default_metric_key == "net_profit"


# ── (1) WB: two candidates/decisions; Ozon: only set_price (rd unavailable) ───

async def _promote(db, uid):
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


def test_wb_two_decisions_set_price_and_reduce_discount():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, mp="wb")
        await _promote(db, uid)
        decs = (await db.execute(select(Decision))).scalars().all()
        assert {d.action_key for d in decs} == {"set_price", RD}
        assert len({d.insight_key for d in decs}) == 1     # same insight_key
    _run(go())


def test_ozon_only_set_price_reduce_discount_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, mp="ozon")
        res = await _promote(db, uid)
        decs = (await db.execute(select(Decision))).scalars().all()
        assert {d.action_key for d in decs} == {"set_price"}   # reduce_discount honest-unavailable
        # the reduce_discount link exists but was capability-skipped (no Decision)
        links = (await db.execute(select(EngineSignalDecisionLink))).scalars().all()
        assert {l.action_key for l in links} == {"set_price", RD}
        assert all(l.decision_id is None for l in links if l.action_key == RD)
    _run(go())


# ── (6) re-run idempotent ────────────────────────────────────────────────────

def test_rerun_no_duplicates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, mp="wb")
        await _promote(db, uid)
        await _promote(db, uid)
        assert len((await db.execute(select(Decision))).scalars().all()) == 2
        assert len((await db.execute(select(EngineSignalDecisionLink))).scalars().all()) == 2
    _run(go())


# ── WB reduce_discount full loop: apply → measure net_profit → improved → Learning ─

def test_wb_reduce_discount_full_loop(monkeypatch):
    import tasks.check_pricing as cp
    monkeypatch.setattr(cp, "compute_recommendation",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("forbidden")))
    seen = []
    async def fake(*, token, offer_id, discount):
        seen.append((offer_id, discount)); return {"requestId": "rq"}
    monkeypatch.setattr(wb_client, "set_discount", fake)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); sku = "SKU1"
        await _seed(db, uid, mp="wb", sku=sku)
        await _promote(db, uid)
        rd_dec = (await db.execute(select(Decision).where(
            Decision.action_key == RD))).scalars().one()

        # preview: payload {offer_id, discount:0}; no adapter call
        p = await build_apply_preview(db, user_id=uid, decision_id=rd_dec.id,
                                      marketplace="wb", sku=sku)
        assert p.applyable is True and p.action_key == RD
        assert p.payload == {"offer_id": "SKU1", "discount": 0}
        assert p.safety_class == "manual_approval" and seen == []

        # apply → wb set_discount(0) once
        res = await execute_bound_decision(db, user_id=uid, decision_id=rd_dec.id,
                                           marketplace="wb", sku=sku, dry_run=False)
        assert res.ok and res.status == "success" and seen == [("SKU1", 0)]

        # measure net_profit → recover → improved (for the reduce_discount link)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        rd_link = (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.action_key == RD))).scalars().one()
        obs = (await db.execute(select(EngineEffectObservation).where(
            EngineEffectObservation.link_id == rd_link.id))).scalars().one()
        assert obs.metric_key == "net_profit"
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date="2026-06-20", sku=sku, revenue=10000.0, net_profit=500.0))
        await db.commit()
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation).where(
            EngineEffectObservation.link_id == rd_link.id))).scalars().one()
        assert obs.effect_band == IMPROVED

        # (10) Learning OS: reduce_discount has its own action_key bucket
        summ = await get_action_learning_summary(db, user_id=uid, marketplace="wb", action_key=RD)
        assert summ.improved_count == 1 and summ.action_key == RD
    _run(go())
