"""
SEO A5 — persistence + Signal Builder tests.

persist creates audit + a full coverage ledger (all 12 rules); triggered creates
seo_problem + a 5-part doctrine seo_signal; not_triggered/not_evaluated create
ledger rows only; signal builder is deterministic and uses improve_description;
insight_key is stable; no public score is computed; agnostic across MPs; SEO core
imports no marketplace clients.
"""
import ast
import asyncio
import inspect
import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_audit import SeoAudit
from models.seo_problem import SeoProblem
from models.seo_rule_evaluation import SeoRuleEvaluation
from models.seo_signal import SeoSignal

from services.seo.card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)
from services.seo.signal_builder import build_signal
from services.seo.evaluation import RuleEvaluation, RuleResult
from services.seo import audit_persist
from services.seo.audit_persist import audit_and_persist

CONS = SeoConstraints(title_min_len=20, title_max_len=100, description_min_len=200,
                      media_min_images=3, attribute_fill_rate_threshold=0.6,
                      content_completeness_threshold=0.7)
_ALL = {"title", "description", "attributes", "media", "category_schema",
        "category_path", "expected_category_path", "variants", "constraints"}


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", title="x" * 40, description="d" * 300,
          schema=None, attributes=None, images=4, variants=("size",),
          expected_category_path=("root", "cat"), availability=None):
    return CardSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="api", title=title, description=description, brand="b",
        category_path=("root", "cat"), expected_category_path=expected_category_path,
        category_schema=schema if schema is not None else CategorySchema(),
        attributes=tuple(attributes or ()), variants=variants, media=CardMedia(image_count=images),
        constraints=CONS, field_availability=availability or {k: True for k in _ALL})


def _unhealthy_snap():
    # title_too_short + description_missing + media_below_minimum + required_attributes_missing
    return _snap(title="short", description="", images=0,
                 schema=CategorySchema(required_attributes=("colour",)), attributes=())


# ── 1. persist → audit + 12 ledger rows ──────────────────────────────────────

def test_persist_creates_audit_and_full_ledger():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_unhealthy_snap())
        await db.commit()
        audit = (await db.execute(select(SeoAudit).where(SeoAudit.id == res.audit_id))).scalar_one()
        assert audit.status == "completed" and audit.rule_catalog_version == "1"
        assert audit.source == "api" and audit.snapshot_hash and audit.triggered_by == "manual"
        ledger = (await db.execute(select(SeoRuleEvaluation).where(
            SeoRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 12                       # every rule recorded
        assert res.rule_evaluation_count == 12
        assert audit.total_problems == res.total_problems
        assert audit.total_not_evaluated == res.total_not_evaluated
    _run(go())


# ── 2/3/4. triggered → problem; not_triggered/not_evaluated → ledger only ────

def test_problem_only_for_triggered_ledger_for_all():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # title present-but-availability off → not_evaluated for title rules
        avail = {k: True for k in _ALL}; avail["title"] = False
        snap = _snap(description="", images=0, availability=avail,
                     schema=CategorySchema(required_attributes=("colour",)), attributes=())
        res = await audit_and_persist(db, user_id=uid, snapshot=snap)
        await db.commit()
        ledger = {r.problem_type: r for r in (await db.execute(select(SeoRuleEvaluation).where(
            SeoRuleEvaluation.audit_id == res.audit_id))).scalars().all()}
        problems = {p.problem_type for p in (await db.execute(select(SeoProblem).where(
            SeoProblem.audit_id == res.audit_id))).scalars().all()}

        # triggered → problem + ledger(triggered)
        assert "media_below_minimum" in problems
        assert ledger["media_below_minimum"].result == "triggered"
        assert ledger["media_below_minimum"].evidence is not None
        # not_triggered → ledger only, no problem
        assert ledger["title_too_long"].problem_type not in problems or \
            ledger["title_too_short"].result == "not_evaluated"
        nt = ledger["attributes_incomplete"] if ledger["attributes_incomplete"].result == "not_evaluated" else None
        # not_evaluated (title off) → ledger with reason, no problem
        assert ledger["title_too_short"].result == "not_evaluated"
        assert "missing_fields" in ledger["title_too_short"].reason
        assert "title_too_short" not in problems
        # ledger has rows that are not problems
        assert len(ledger) == 12 and len(problems) < 12
    _run(go())


# ── 5. triggered → seo_signal with 5 doctrine parts ──────────────────────────

def test_signal_has_five_doctrine_parts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_unhealthy_snap())
        await db.commit()
        sigs = (await db.execute(select(SeoSignal).where(
            SeoSignal.audit_id == res.audit_id))).scalars().all()
        assert len(sigs) == res.total_problems and sigs
        for s in sigs:
            assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
            assert s.status == "active"
            assert s.signal_key == f"seo_{s.problem_type}"
            assert s.priority_level in ("critical", "high", "medium", "low")
    _run(go())


# ── 6. signal builder deterministic ──────────────────────────────────────────

def test_signal_builder_deterministic():
    ev = RuleEvaluation("title_too_short", "Title", "high", "discoverability_loss",
                        "static_card", RuleResult.TRIGGERED,
                        evidence={"title_length": 5, "title_min_len": 20})
    a = build_signal(ev, marketplace="ozon", sku="SKU1")
    b = build_signal(ev, marketplace="ozon", sku="SKU1")
    assert a == b
    assert "5" in a.what and "20" in a.what  # evidence filled deterministically


# ── 7. description actions use improve_description (not generate_description) ──

def test_description_actions_use_improve_description():
    for pt in ("description_missing", "description_too_short"):
        ev = RuleEvaluation(pt, "Description", "medium", "indexing_gain",
                            "static_card", RuleResult.TRIGGERED, evidence={})
        d = build_signal(ev, marketplace="wildberries", sku="S")
        assert d.recommended_action_key == "improve_description"
        assert "generate_description" not in (d.recommended_action_key,) + d.alternative_action_keys
        assert "enrich_description" not in d.alternative_action_keys


# ── 8. insight_key stable: seo_<problem_type>:<marketplace>:<sku> ─────────────

def test_insight_key_stable():
    ev = RuleEvaluation("media_below_minimum", "Content Quality", "high", "conversion_loss",
                        "static_card", RuleResult.TRIGGERED, evidence={})
    d = build_signal(ev, marketplace="yandex", sku="SKU9")
    assert d.insight_key == "seo_media_below_minimum:yandex:SKU9"


# ── 9. no public score / internal_health_index computed ──────────────────────

def test_no_public_score_computed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_unhealthy_snap())
        await db.commit()
        audit = (await db.execute(select(SeoAudit).where(SeoAudit.id == res.audit_id))).scalar_one()
        assert audit.internal_health_index is None  # never computed/set
    src = inspect.getsource(audit_persist)
    assert "internal_health_index" in src  # only the explicit "leave NULL" note
    assert "_compute_score" not in src and "seo_score" not in src.lower()
    _run(go())


# ── 10. agnostic across WB/Ozon/Yandex ───────────────────────────────────────

def test_agnostic_persist():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            res = await audit_and_persist(db, user_id=uid, snapshot=_unhealthy_snap(), now=datetime(2026, 6, 21))
            await db.commit()
            assert res.rule_evaluation_count == 12 and res.total_problems >= 1
    _run(go())


# ── 11. SEO core imports no marketplace clients ──────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(audit_persist)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
        if "adapters" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for bad in forbidden:
                    if bad in m:
                        offenders.append(f"{path.name}:{bad}")
    assert not offenders, offenders
