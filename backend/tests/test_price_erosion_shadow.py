"""
Price Erosion / Discount Creep Diagnosis — SHADOW VALIDATION (Phase 8.1), read-only.

Exercises the DISABLED price_erosion producer via run_one() on realistic dated
ImportedProductRow.price snapshots. Validation tests only — no production code touched. Covers
the self-referential relative-drop band matrix (5%/15%/30% + exact boundaries), honest absence
(thin history, flat/rising, sub-band, non-positive baseline, single-dip noise), dated ordering
(by created_at, NOT insert order), idempotence, evidence determinism, stale/lifecycle reconcile,
advisory-only side-effect freedom (no price-write), disabled→no-scheduler, not-in-feed, and
INDEPENDENCE from the executable Pricing contour (never touches pricing_signal).

Observed-only: relative_drop = (baseline − latest) / baseline. No forecast, no benchmark, no
competitor compare, no price-write action.
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
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.price_erosion_signal import PriceErosionSignal
from models.price_erosion_audit import PriceErosionAudit
from models.pricing_signal import PricingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.price_erosion.diagnosis_source import classify_price_erosion
from services.price_erosion.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, dated_prices, *, marketplace="wildberries", sku="SKU1"):
    """dated_prices = list of (day, price). One finance row (candidacy) + one dated
    ImportedProductRow snapshot per entry (created_at = 2026-06-<day>)."""
    db.add(ImportedFinanceRow(import_id="fin0", user_id=uid, marketplace=marketplace,
                              sku=sku, date="2026-06-01", quantity=1, revenue=100.0, net_profit=0.0))
    for day, price in dated_prices:
        db.add(ImportedProductRow(import_id=f"imp{day}", user_id=uid, marketplace=marketplace,
                                  sku=sku, price=price, created_at=datetime(2026, 6, day)))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="price_erosion", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(PriceErosionSignal).where(
        PriceErosionSignal.user_id == uid,
        PriceErosionSignal.status.in_(("active", "reopened"))))).scalars().all()


# in-order dated seed (day ascending == price chronology)
def _seq(*prices):
    return [(i + 1, p) for i, p in enumerate(prices)]


def _one(*prices, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(*prices), **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── severity-band matrix ──────────────────────────────────────────────────────

def test_high_band():
    sigs = _one(100, 80, 65)                     # drop 0.35 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"
    assert sigs[0].problem_type == "discount_creep"
    assert sigs[0].signal_key == "price_erosion_discount_creep"


def test_medium_band():
    sigs = _one(100, 90, 80)                      # drop 0.20 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_low_band():
    sigs = _one(100, 97, 92)                      # drop 0.08 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── exact boundaries (>=) ─────────────────────────────────────────────────────

def test_boundary_005_is_low():
    sigs = _one(100, 98, 95)                      # drop 0.05 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


def test_boundary_015_is_medium():
    sigs = _one(100, 90, 85)                      # drop 0.15 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_030_is_high():
    sigs = _one(100, 75, 70)                      # drop 0.30 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


# ── honest absence ────────────────────────────────────────────────────────────

def test_absence_sub_band():
    assert _one(100, 99, 97) == []               # drop 0.03 (<5%) → nothing


def test_absence_flat():
    assert _one(100, 100, 100) == []             # no drift → nothing


def test_absence_rising():
    assert _one(90, 95, 100) == []               # price rising → nothing


def test_absence_thin_history():
    assert _one(100, 80) == []                   # only 2 snapshots → nothing


def test_absence_single_dip_noise_rejected():
    # latest dropped, but the snapshot before it was still at baseline → single-dip noise
    assert _one(100, 100, 70) == []              # prev >= baseline → not confirmed


def test_absence_non_positive_baseline():
    assert _one(0, 50, 40) == []                 # baseline 0 → cannot ratio → nothing


# ── dated ordering, not insert order ─────────────────────────────────────────

def test_dated_ordering_not_insert_order():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # insert scrambled; created_at defines chronology → [100(day1), 80(day10), 65(day20)]
        await _seed(db, uid, [(20, 65), (1, 100), (10, 80)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].priority_level == "high"   # drop 0.35 confirmed
    _run(go())


# ── advisory-only + INDEPENDENCE from Pricing (no price-write) ───────────────

def test_advisory_only_and_pricing_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(100, 80, 65)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 1
        # no downstream executable / decision artifacts (no price-write)
        for M in (Decision, EngineSignalDecisionLink, ExecutionLog):
            assert (await db.execute(select(M))).scalars().all() == []
        # never writes the executable Pricing contour's table
        assert (await db.execute(select(PricingSignal))).scalars().all() == []
        sig = (await _live(db, uid))[0]
        assert sig.recommended_action_key is None
        assert sig.effect_type == "margin_compression" and sig.category == "price_erosion"
        assert sig.insight_key == "price_erosion_discount_creep:wildberries:SKU1"
    _run(go())


# ── idempotence + evidence determinism ───────────────────────────────────────

def test_idempotent_rerun_no_duplicate():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(100, 80, 65)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(PriceErosionAudit).where(
            PriceErosionAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2                            # append-only audit per run
    _run(go())


def test_evidence_hash_deterministic():
    d1 = classify_price_erosion([100.0, 80.0, 65.0], marketplace="wb", sku="S")
    d2 = classify_price_erosion([100.0, 80.0, 65.0], marketplace="wb", sku="S")
    assert d1 is not None
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
    assert d1.evidence["relative_drop"] == 0.35
    assert d1.evidence["baseline_price"] == 100.0 and d1.evidence["latest_price"] == 65.0


# ── lifecycle: resolved reopens; dismissed changed-evidence reopens ──────────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(100, 80, 65)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(100, 80, 65)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []                # unchanged — same evidence
    _run(go())


# ── disabled: registered but never scheduled ─────────────────────────────────

def test_registry_price_erosion_disabled():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "price_erosion" in by_key
    assert by_key["price_erosion"].enabled is False


def test_scheduler_does_not_run_price_erosion():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(100, 80, 65)); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        from models.advisory_run import AdvisoryRun
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "price_erosion" not in keys               # disabled → never scheduled
    _run(go())


# ── in the Decision Feed (reader wired Phase 8.3a; producer still disabled) ───

def test_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "price_erosion_signal" in tables              # reader wired (8.3a); INERT until 8.3b
    assert "pricing_signal" in tables                    # executable Pricing still wired, independent
