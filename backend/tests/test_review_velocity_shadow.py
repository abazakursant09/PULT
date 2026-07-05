"""
Review Acquisition / Social-Proof Velocity Diagnosis — SHADOW VALIDATION (Phase 6.1),
read-only.

Exercises the review_velocity producer via run_one() on realistic dated reviews_count
snapshots + finance quantity rows. Validation tests only — no production code touched. Covers
the self-referential band matrix (relative_drop 0.30/0.50/0.70 + exact boundaries), honest
absence (thin history, insufficient per-window sales, earlier_rate==0, flat/accelerating,
sub-band, reviews-decrease bad data, unalignable windows), idempotence, evidence determinism,
stale/lifecycle reconcile, advisory-only side-effect freedom, enabled→scheduler-runs (Phase
6.3b), and INDEPENDENCE from BOTH Rating and Review (never touches rating_signal /
review_signal).

The metric is self-referential: relative_drop = (earlier_rate − recent_rate) / earlier_rate,
where each window's rate = reviews_gained / units_sold. No absolute floor, benchmark, or
competitor compare.
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
from models.review_velocity_signal import ReviewVelocitySignal
from models.review_velocity_audit import ReviewVelocityAudit
from models.rating_signal import RatingSignal
from models.review_signal import ReviewSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.review_velocity.diagnosis_source import classify_review_velocity_stall
from services.review_velocity.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, snaps, sales, *, marketplace="wildberries", sku="SKU1"):
    """snaps = [(day, reviews_count)] → dated ImportedProductRow (created_at=2026-06-day).
    sales = [(day, quantity)] → ImportedFinanceRow (date="2026-06-day"). Finance also
    provides (marketplace, sku) candidacy."""
    for day, rc in snaps:
        db.add(ImportedProductRow(import_id=f"p{day}", user_id=uid, marketplace=marketplace,
                                  sku=sku, reviews_count=rc, created_at=datetime(2026, 6, day)))
    for day, qty in sales:
        db.add(ImportedFinanceRow(import_id=f"f{day}", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{day:02d}", quantity=qty,
                                  revenue=100.0, net_profit=0.0))
    await db.flush()


def _scenario(earlier_reviews, earlier_units, recent_reviews, recent_units):
    """3 snapshots at days 1/10/20; earlier sales on day 5 (window H1 = [06-01, 06-10)),
    recent sales on day 15 (window H2 = [06-10, 06-20]). reviews_count monotonic."""
    base = 100
    snaps = [(1, base), (10, base + earlier_reviews),
             (20, base + earlier_reviews + recent_reviews)]
    sales = [(5, earlier_units), (15, recent_units)]
    return snaps, sales


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="review_velocity", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(ReviewVelocitySignal).where(
        ReviewVelocitySignal.user_id == uid,
        ReviewVelocitySignal.status.in_(("active", "reopened"))))).scalars().all()


def _one(snaps, sales, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, snaps, sales, **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── severity-band matrix (earlier_rate = 1.0 in every scenario) ──────────────

def test_high_band():
    sigs = _one(*_scenario(10, 10, 2, 10))          # recent_rate 0.2 → drop 0.8 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_medium_band():
    sigs = _one(*_scenario(10, 10, 4, 10))          # recent_rate 0.4 → drop 0.6 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_low_band():
    sigs = _one(*_scenario(10, 10, 6, 10))          # recent_rate 0.6 → drop 0.4 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── exact boundaries (>=) ─────────────────────────────────────────────────────

def test_boundary_070_is_high():
    sigs = _one(*_scenario(10, 10, 3, 10))          # recent_rate 0.3 → drop 0.70 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_boundary_050_is_medium():
    sigs = _one(*_scenario(10, 10, 5, 10))          # recent_rate 0.5 → drop 0.50 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_030_is_low():
    sigs = _one(*_scenario(10, 10, 7, 10))          # recent_rate 0.7 → drop 0.30 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── honest absence ────────────────────────────────────────────────────────────

def test_absence_sub_band():
    assert _one(*_scenario(10, 10, 8, 10)) == []    # recent_rate 0.8 → drop 0.20 → nothing


def test_absence_flat_rate():
    assert _one(*_scenario(10, 10, 10, 10)) == []   # recent_rate == earlier_rate → nothing


def test_absence_accelerating():
    assert _one(*_scenario(5, 10, 10, 10)) == []    # recent_rate > earlier_rate → nothing


def test_absence_earlier_rate_zero():
    assert _one(*_scenario(0, 10, 5, 10)) == []     # earlier_gained 0 → earlier_rate 0 → nothing


def test_absence_thin_history():
    snaps = [(1, 100), (10, 108)]                   # only 2 snapshots
    sales = [(5, 10), (15, 10)]
    assert _one(snaps, sales) == []


def test_absence_insufficient_earlier_sales():
    assert _one(*_scenario(10, 4, 2, 10)) == []     # earlier_units 4 < MIN_WINDOW_UNITS


def test_absence_insufficient_recent_sales():
    assert _one(*_scenario(10, 10, 2, 4)) == []     # recent_units 4 < MIN_WINDOW_UNITS


def test_absence_reviews_decrease_bad_data():
    snaps = [(1, 100), (10, 110), (20, 105)]        # recent_gained = -5 → bad data
    sales = [(5, 10), (15, 10)]
    assert _one(snaps, sales) == []


def test_absence_unalignable_same_day():
    snaps = [(1, 100), (1, 110), (1, 112)]          # all same day → first_date == mid_date
    sales = [(1, 20)]
    assert _one(snaps, sales) == []


# ── self-referential correctness: evidence carries own-history windows ───────

def test_evidence_is_self_referential():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        # doctrine text compares to the product's OWN earlier rate — no floor/benchmark/competitor
        assert "собственн" in sig.what            # "относительно собственного прежнего темпа"
        assert sig.effect_type == "social_proof_stall"
        assert sig.category == "review_velocity"
        assert sig.recommended_action_key is None
        assert sig.insight_key == "review_velocity_stall:wildberries:SKU1"
    _run(go())


# ── idempotence + evidence determinism ───────────────────────────────────────

def test_idempotent_rerun_no_duplicate():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(ReviewVelocityAudit).where(
            ReviewVelocityAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2                        # append-only audit per run
    _run(go())


def test_evidence_hash_deterministic():
    snaps, sales = _scenario(10, 10, 2, 10)
    diag = classify_review_velocity_stall(
        [(f"2026-06-{d:02d}", rc) for d, rc in snaps],
        {f"2026-06-{d:02d}": q for d, q in sales},
        marketplace="wildberries", sku="SKU1")
    assert diag is not None
    assert evidence_hash(diag.evidence) == evidence_hash(diag.evidence)
    assert diag.evidence["relative_drop"] == 0.8
    assert diag.evidence["earlier_review_rate"] == 1.0
    assert diag.evidence["recent_review_rate"] == 0.2


# ── lifecycle: resolved reopens on re-detect; dismissed stays dismissed ───────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
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
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []            # unchanged — same evidence
    _run(go())


# ── advisory-only + INDEPENDENCE from Rating and Review ───────────────────────

def test_advisory_only_and_rating_review_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 1
        # no downstream executable / decision artifacts
        for M in (Decision, EngineSignalDecisionLink, ExecutionLog):
            assert (await db.execute(select(M))).scalars().all() == []
        # never writes another contour's table
        assert (await db.execute(select(RatingSignal))).scalars().all() == []
        assert (await db.execute(select(ReviewSignal))).scalars().all() == []
    _run(go())


# ── enabled: registered and scheduled (Phase 6.3b) ───────────────────────────

def test_registry_review_velocity_enabled():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "review_velocity" in by_key
    assert by_key["review_velocity"].enabled is True


def test_scheduler_runs_review_velocity():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        snaps, sales = _scenario(10, 10, 2, 10)
        await _seed(db, uid, snaps, sales); await db.commit()
        # slot_budget covers all enabled producers in one tick (11 now — default 10 would
        # drop the last-registered producer)
        await AdvisoryRuntime().run_due_producers(db, now=NOW, slot_budget=20)   # REAL registry
        from models.advisory_run import AdvisoryRun
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "review_velocity" in keys             # enabled → scheduled
    _run(go())


# ── in the Decision Feed (reader wired Phase 6.3a; producer still disabled) ───

def test_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "review_velocity_signal" in tables        # reader wired (6.3a); INERT until 6.3b
    assert "rating_signal" in tables                 # Rating still wired, independent
    assert "review_signal" in tables                 # Review still wired, independent
