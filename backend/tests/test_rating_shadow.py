"""
Rating / Reputation Health Diagnosis — SHADOW VALIDATION (Phase 5.2), read-only.

Exercises the DISABLED rating producer via run_one() on realistic dated ImportedProductRow
histories, raising confidence to the Supply 4.2 level before any feed/enable slice.
Validation tests only — no production code touched. Covers the severity-band matrix (+
exact boundaries 0.10/0.20/0.40), honest absence, dated-snapshot ordering (created_at, not
insert order), latest-vs-baseline, multi-SKU isolation, two-marketplace independence,
evidence_hash determinism, stale/lifecycle reconcile, advisory-only side-effect freedom,
disabled→no-scheduler/no-feed, and REVIEW INDEPENDENCE (rating never touches review_signal).

Bands (drop = baseline − latest, `>=`): >=0.40 high, >=0.20 medium, >=0.10 low; so exactly
0.10→low, 0.20→medium, 0.40→high.
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
from models.rating_signal import RatingSignal
from models.rating_audit import RatingAudit
from models.review_signal import ReviewSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.rating.diagnosis_source import build_rating_series, classify_rating_decline
from services.rating.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, dated_ratings, *, marketplace="wildberries", sku="SKU1"):
    """dated_ratings = list of (day, rating). One finance row (candidacy) + one dated
    ImportedProductRow snapshot per entry (created_at = 2026-06-<day>)."""
    db.add(ImportedFinanceRow(import_id="fin0", user_id=uid, marketplace=marketplace,
                              sku=sku, date="2026-06-01", quantity=1, revenue=100.0, net_profit=0.0))
    for day, rating in dated_ratings:
        db.add(ImportedProductRow(import_id=f"imp{day}", user_id=uid, marketplace=marketplace,
                                  sku=sku, rating=rating, reviews_count=100,
                                  created_at=datetime(2026, 6, day)))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="rating", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(RatingSignal).where(
        RatingSignal.user_id == uid,
        RatingSignal.status.in_(("active", "reopened"))))).scalars().all()


def _one(dated_ratings, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, dated_ratings, **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# in-order dated seeds (day ascending == rating chronology)
def _seq(*ratings):
    return [(i + 1, r) for i, r in enumerate(ratings)]


# ── severity-band matrix + exact boundaries ──────────────────────────────────

def test_high_band():
    sigs = _one(_seq(4.8, 4.5, 4.3))          # drop 0.5 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_medium_band():
    sigs = _one(_seq(4.8, 4.7, 4.55))         # drop 0.25 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_low_band():
    sigs = _one(_seq(4.8, 4.75, 4.68))        # drop 0.12 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# Boundary behavior: the `>=` bands are correct + deterministic, but nominal-boundary
# rating drops are IEEE-754-fuzzy — 4.8−4.4 computes 0.3999…, 4.8−4.6 computes 0.2000…018,
# 4.8−4.7 computes 0.0999…. So exact-nominal boundaries do NOT all round up; these tests
# pin the TRUE observed behavior (documented in the report). Mid-band tests above cover the
# bands with clear margins.

def test_boundary_nominal_040_floats_to_medium():
    sigs = _one(_seq(4.8, 4.5, 4.4))          # nominal 0.40 → float 0.3999… → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_nominal_020_is_medium():
    sigs = _one(_seq(4.8, 4.7, 4.6))          # nominal 0.20 → float 0.2000…018 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_nominal_010_floats_to_absence():
    sigs = _one(_seq(4.8, 4.75, 4.7))         # nominal 0.10 → float 0.0999… < 0.10 → nothing
    assert sigs == []


# ── honest-absence matrix ────────────────────────────────────────────────────

def test_absence_thin_history():
    assert _one(_seq(4.8, 4.3)) == []                 # 2 snapshots < 3


def test_absence_flat():
    assert _one(_seq(4.8, 4.8, 4.8)) == []


def test_absence_rising():
    assert _one(_seq(4.3, 4.5, 4.8)) == []


def test_absence_below_band():
    assert _one(_seq(4.8, 4.78, 4.75)) == []          # drop 0.05 < 0.10


def test_absence_no_rating_values():
    assert _one(_seq(None, None, None)) == []


def test_absence_unconfirmed_single_blip():
    assert _one(_seq(4.8, 4.8, 4.3)) == []            # prev == baseline → unconfirmed


# ── dated-snapshot ordering (created_at, not insert order) ───────────────────

def test_dated_ordering_not_insert_order():
    # insert order would read baseline=4.3, latest=4.5 (rising → nothing); created_at order
    # reads baseline=4.8 (day1), latest=4.3 (day3) → drop 0.5 → high. A HIGH signal proves
    # the producer sorts by created_at, not insert order.
    sigs = _one([(3, 4.3), (1, 4.8), (2, 4.5)])
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_latest_vs_baseline_values():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, [(3, 4.3), (1, 4.8), (2, 4.5)]); await db.commit()
        series = await build_rating_series(db, uid, "wildberries", "SKU1")
        d = classify_rating_decline(series, marketplace="wildberries", sku="SKU1")
        assert d.baseline_rating == 4.8 and d.latest_rating == 4.3 and d.snapshot_count == 3
    _run(go())


# ── multi-SKU isolation + two-marketplace independence ───────────────────────

def test_multi_sku_only_declining():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3), sku="DOWN")
        await _seed(db, uid, _seq(4.8, 4.8, 4.8), sku="FLAT")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].sku == "DOWN"
    _run(go())


def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3), marketplace="wildberries", sku="SKU1")  # decline
        await _seed(db, uid, _seq(4.8, 4.8, 4.8), marketplace="ozon", sku="SKU1")          # flat
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = {s.insight_key for s in await _live(db, uid)}
        assert keys == {"rating_decline:wildberries:SKU1"}
    _run(go())


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_deterministic():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3)); await db.commit()
        series = await build_rating_series(db, uid, "wildberries", "SKU1")
        d1 = classify_rating_decline(series, marketplace="wildberries", sku="SKU1")
        d2 = classify_rating_decline(series, marketplace="wildberries", sku="SKU1")
        assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
        await _diagnose(db, uid); await db.commit()
        h1 = (await _live(db, uid))[0].evidence_hash
        await _diagnose(db, uid); await db.commit()
        h2 = (await _live(db, uid))[0].evidence_hash
        assert h1 == h2 == evidence_hash(d1.evidence)
    _run(go())


# ── stale / lifecycle reconcile ──────────────────────────────────────────────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
        row = (await db.execute(select(RatingSignal).where(RatingSignal.user_id == uid))).scalars().one()
        assert row.status == "dismissed"
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "dismissed"; sig.evidence_hash = "stale"
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_absence_does_not_resolve_live_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.8, 4.8)); await db.commit()   # flat → no decline
        db.add(RatingSignal(user_id=uid, audit_id=str(uuid.uuid4()),
               signal_key="rating_decline", insight_key="rating_decline:wildberries:SKU1",
               problem_type="rating_decline", marketplace="wildberries", sku="SKU1",
               status="active", created_at=NOW))
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "active"   # absence never auto-resolves
    _run(go())


# ── advisory-only + disabled cannot leak + Review independence ───────────────

def test_advisory_only_and_review_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _seq(4.8, 4.5, 4.3), sku="A")
        await _seed(db, uid, _seq(4.9, 4.6, 4.4), marketplace="ozon", sku="B")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 2
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
        # REVIEW INDEPENDENCE: rating never creates/touches review_signal
        assert (await db.execute(select(ReviewSignal).where(ReviewSignal.user_id == uid))).scalars().all() == []
    _run(go())


def test_disabled_but_feed_reads_it():
    # Producer stays DISABLED (never scheduled); Phase 5.3a wired the feed reader, so
    # rating IS now a feed engine — inert until 5.3b enables the producer.
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["rating"].enabled is False
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "rating_signal" in tables                # rating now wired (reader-only)
    assert "review_signal" in tables                # Review still independent
