"""
Returns-Reason Diagnosis — reason_share_rise_<bucket> (small slice inside Returns).

Observed COMPOSITION drift: the share of returns carrying a specific reported reason
bucket rising over time, self-referential, counts-only. Orthogonal to return_rate_rise
(frequency). Advisory-only, DB-only, no marketplace path, no money.

Reuses the enabled returns producer via run_one() — no new producer/registry/feed/migration.
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
from models.imported_return import ImportedReturnRow
from models.returns_signal import ReturnsSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.returns.reason_taxonomy import normalize_return_reason
from services.returns.diagnosis_source import (
    returns_by_reason_by_date, classify_reason_share_drifts)
from services.decision_feed.builder import build_feed

NOW = datetime(2026, 6, 30, 12, 0, 0)
MP = "wildberries"
SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _finance(db, uid, dates_qty, *, marketplace=MP, sku=SKU):
    """dates_qty: [(date, units_sold)] — anchors sku as a candidate (+ return_rate denominator)."""
    for date, qty in dates_qty:
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=date, quantity=int(qty), revenue=100.0,
                                  net_profit=0.0))
    await db.flush()


async def _returns(db, uid, rows, *, marketplace=MP, sku=SKU):
    """rows: [(date, reason, returns_qty)] — return_amount set NON-zero to prove it is ignored."""
    for date, reason, qty in rows:
        db.add(ImportedReturnRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                 sku=sku, date=date, returns_qty=int(qty),
                                 return_amount=9999.0, reason=reason))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="returns", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(ReturnsSignal).where(
        ReturnsSignal.user_id == uid,
        ReturnsSignal.status.in_(("active", "reopened"))))).scalars().all()


# a single finance date → return_rate_rise cannot form (needs ≥3 sale days), so reason-share
# is isolated. Enough to make the sku a candidate.
_ANCHOR = [("2026-06-01", 100)]

# 4 distinct return days; split mid → earlier [01,02], recent [03,04].
_DEFECT_RISE = [("2026-06-01", "брак", 2), ("2026-06-01", "спасибо, всё ок", 4),
                ("2026-06-02", "дефект", 1), ("2026-06-02", "спасибо", 3),
                ("2026-06-03", "брак", 4), ("2026-06-03", "спасибо", 1),
                ("2026-06-04", "товар бракованный", 4), ("2026-06-04", "спасибо", 1)]
# earlier defect 3 / total 10 = .30 ; recent defect 8 / total 10 = .80 → +167% high


# ── detection per bucket ──────────────────────────────────────────────────────

def test_rising_quality_defect_share():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.problem_type == "reason_share_rise_quality_defect"
        assert s.signal_key == "returns_reason_share_rise_quality_defect"
        assert s.insight_key == "returns_reason_share_rise_quality_defect:wildberries:SKU1"
        assert s.effect_type == "return_reason_shift" and s.priority_level == "high"
    _run(go())


def test_rising_size_fit_share():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        rows = [("2026-06-01", "размер не подошёл", 2), ("2026-06-01", "спасибо", 4),
                ("2026-06-02", "мал", 1), ("2026-06-02", "спасибо", 3),
                ("2026-06-03", "великоват размер", 4), ("2026-06-03", "спасибо", 1),
                ("2026-06-04", "плохая посадка", 4), ("2026-06-04", "спасибо", 1)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert [s.problem_type for s in sigs] == ["reason_share_rise_size_fit"]
    _run(go())


def test_rising_mismatch_description_share():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        rows = [("2026-06-01", "не соответствует", 2), ("2026-06-01", "спасибо", 4),
                ("2026-06-02", "не то прислали", 1), ("2026-06-02", "спасибо", 3),
                ("2026-06-03", "другой товар", 4), ("2026-06-03", "спасибо", 1),
                ("2026-06-04", "отличается от описания", 4), ("2026-06-04", "спасибо", 1)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert [s.problem_type for s in await _live(db, uid)] == \
            ["reason_share_rise_mismatch_description"]
    _run(go())


# ── non-diagnosed buckets never signal ───────────────────────────────────────

def test_unknown_and_empty_reasons_never_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # 'other' (unmapped) + 'unspecified' (empty) shares rising — never produce a signal
        rows = [("2026-06-01", "спасибо", 3), ("2026-06-01", None, 2),
                ("2026-06-02", "хорошо", 2), ("2026-06-02", "", 3),
                ("2026-06-03", "спасибо", 8), ("2026-06-03", None, 2),
                ("2026-06-04", "отлично", 7), ("2026-06-04", "", 1)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_high_unspecified_share_honest_absence():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # defect share DOES rise, but >50% of returns carry no reason → whole-sku absence
        rows = [("2026-06-01", "брак", 3), ("2026-06-01", None, 12),
                ("2026-06-02", None, 0),
                ("2026-06-03", "брак", 9), ("2026-06-03", None, 12),
                ("2026-06-04", None, 0)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_rare_bucket_no_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # defect below MIN_BUCKET_RETURNS_WINDOW (3) in the windows → not diagnosable
        rows = [("2026-06-01", "брак", 1), ("2026-06-01", "спасибо", 9),
                ("2026-06-02", "спасибо", 0),
                ("2026-06-03", "брак", 2), ("2026-06-03", "спасибо", 8),
                ("2026-06-04", "спасибо", 0)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_flat_composition_no_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        rows = [("2026-06-01", "брак", 5), ("2026-06-01", "спасибо", 5),
                ("2026-06-02", "спасибо", 0),
                ("2026-06-03", "брак", 5), ("2026-06-03", "спасибо", 5),
                ("2026-06-04", "спасибо", 0)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── coexistence + orthogonality with return_rate_rise ────────────────────────

def test_return_rate_rise_and_reason_share_coexist():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # finance ≥3 sale days, flat 50 units → returns 5→15 raises the RATE; defect share also rises
        await _finance(db, uid, [("2026-06-01", 50), ("2026-06-02", 50),
                                 ("2026-06-03", 50), ("2026-06-04", 50)])
        rows = [("2026-06-01", "брак", 2), ("2026-06-01", "спасибо", 1),
                ("2026-06-02", "брак", 1), ("2026-06-02", "спасибо", 1),
                ("2026-06-03", "брак", 6), ("2026-06-03", "спасибо", 2),
                ("2026-06-04", "брак", 6), ("2026-06-04", "спасибо", 1)]
        await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        pts = {s.problem_type for s in await _live(db, uid)}
        assert "return_rate_rise" in pts
        assert "reason_share_rise_quality_defect" in pts
    _run(go())


def test_composition_shift_with_flat_rate_still_fires():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # flat TOTAL return rate (10 earlier / 10 recent over equal sales) → NO return_rate_rise;
        # but defect share .30 → .80 → reason_share_rise fires
        await _finance(db, uid, [("2026-06-01", 50), ("2026-06-02", 50),
                                 ("2026-06-03", 50), ("2026-06-04", 50)])
        rows = [("2026-06-01", "брак", 1), ("2026-06-01", "спасибо", 4),
                ("2026-06-02", "брак", 2), ("2026-06-02", "спасибо", 3),
                ("2026-06-03", "брак", 4), ("2026-06-03", "спасибо", 1),
                ("2026-06-04", "брак", 4), ("2026-06-04", "спасибо", 1)]
        await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        pts = {s.problem_type for s in await _live(db, uid)}
        assert "reason_share_rise_quality_defect" in pts
        assert "return_rate_rise" not in pts
    _run(go())


# ── counts-only (double-count discipline) ────────────────────────────────────

def test_reason_share_counts_only_no_money():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        by = await returns_by_reason_by_date(db, uid, MP, SKU)
        diags = classify_reason_share_drifts(by, marketplace=MP, sku=SKU)
        assert diags
        ev = diags[0].evidence
        for money_key in ("return_amount", "net_profit", "money", "amount"):
            assert money_key not in ev
        # evidence is counts/shares only
        assert ev["earlier_bucket_returns"] == 3 and ev["recent_bucket_returns"] == 8
    _run(go())


# ── persistence + multiplicity + idempotency ─────────────────────────────────

def test_reason_share_persists_advisory_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        s = (await _live(db, uid))[0]
        assert s.recommended_action_key is None and s.alternative_action_keys is None
        assert s.category == "returns"
        for f in (s.what, s.why, s.meaning, s.what_to_do, s.expected_effect):
            assert f and f.strip() and "{" not in f and "}" not in f
    _run(go())


def test_multiple_rising_buckets_multiple_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # defect .15→.40 and size .15→.30, both over totals of 20 per window
        rows = [("2026-06-01", "брак", 2), ("2026-06-01", "размер", 2), ("2026-06-01", "спасибо", 6),
                ("2026-06-02", "брак", 1), ("2026-06-02", "размер", 1), ("2026-06-02", "спасибо", 8),
                ("2026-06-03", "брак", 4), ("2026-06-03", "размер", 3), ("2026-06-03", "спасибо", 3),
                ("2026-06-04", "брак", 4), ("2026-06-04", "размер", 3), ("2026-06-04", "спасибо", 3)]
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, rows); await db.commit()
        await _diagnose(db, uid); await db.commit()
        pts = {s.problem_type for s in await _live(db, uid)}
        assert pts == {"reason_share_rise_quality_defect", "reason_share_rise_size_fit"}
    _run(go())


def test_rerun_idempotent_one_live_per_insight_key():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        keys = [s.insight_key for s in sigs]
        assert keys == ["returns_reason_share_rise_quality_defect:wildberries:SKU1"]
        assert len(keys) == len(set(keys))
    _run(go())


# ── advisory-only: nothing executable ────────────────────────────────────────

def test_no_executable_side_effects():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid)
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── surfaces through the existing Returns feed reader ────────────────────────

def test_reason_share_surfaces_in_feed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _finance(db, uid, _ANCHOR); await _returns(db, uid, _DEFECT_RISE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid, now=NOW)
        item = [i for i in feed if i.contour == "returns"
                and i.item_key == "returns_reason_share_rise_quality_defect:wildberries:SKU1"]
        assert len(item) == 1
        assert item[0].marketplace == "wildberries" and item[0].sku == "SKU1"
        assert item[0].title and item[0].what_happened and item[0].recommended_action
    _run(go())


# ── taxonomy unit ────────────────────────────────────────────────────────────

def test_reason_taxonomy_mapping():
    assert normalize_return_reason("Товар бракованный") == "quality_defect"
    assert normalize_return_reason("размер не подошёл") == "size_fit"
    assert normalize_return_reason("не соответствует описанию") == "mismatch_description"
    assert normalize_return_reason("помят при доставке") == "damaged_delivery"
    assert normalize_return_reason("просто передумал") == "changed_mind"
    assert normalize_return_reason(None) == "unspecified"
    assert normalize_return_reason("   ") == "unspecified"
    assert normalize_return_reason("случайный текст без ключа") == "other"


# ── alembic head unchanged (no migration) ────────────────────────────────────

def test_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["efp1a2b3c4d01"], heads
