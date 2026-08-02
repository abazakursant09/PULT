"""LEGAL-1B guards — behavioural personalization is gone and cannot come back silently, while the
objective surfaces (/api/today, decision_memory outcome-learning) are untouched.

149-FZ art. 10.2-2: PULT must not collect / systematize / analyse a specific user's preferences to
change the information it shows. These tests fail if any part of the behavioural surface reappears in
production code, and assert that identical business data yields an identical /api/today regardless of
any (now-removed) per-user history.
"""
from __future__ import annotations

import asyncio
import importlib
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest

import models  # registers all tables on Base.metadata
from database import Base

_BACKEND = Path(__file__).resolve().parents[1]
# Production packages only — the alembic migration and the tests legitimately name the dropped
# tables/symbols in their up/down + docstrings and are deliberately excluded.
_PROD_DIRS = ["routers", "services", "models", "schemas", "logic", "tasks"]
_FORBIDDEN = [
    r"\bUserEvent\b",
    r"\bOperatorDecision\b",
    r"\b_PREF_WEIGHTS\b",
    r"\b_compute_preference_scores\b",
    r"\boperator_profile\b",
    r"\bapply_adaptations\b",
    r"\b_focused_filter\b",
    r"\boperator_strategy_profile\b",
    r"/api/events\b",
]


def _prod_py_files():
    files = list(_BACKEND.glob("*.py"))  # main.py, dependencies.py, ...
    for d in _PROD_DIRS:
        files += (_BACKEND / d).rglob("*.py")
    return files


# ── the behavioural models / modules / routes are gone ───────────────────────────

def test_behavioral_models_are_gone():
    assert not hasattr(models, "UserEvent")
    assert not hasattr(models, "OperatorDecision")
    for mod in ("models.user_event", "models.operator_decision",
                "logic.operator_profile", "logic.operator_strategy_profile",
                "logic.decision_weight", "logic.decision_drift", "logic.operational_trajectory",
                "routers.events"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_behavioral_tables_not_in_metadata():
    assert "user_events" not in Base.metadata.tables
    assert "operator_decisions" not in Base.metadata.tables


def test_events_routes_and_get_insights_removed_execute_kept():
    import main
    from routers import action_engine
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not any(p.startswith("/api/events") for p in paths)          # collection endpoint gone
    # dormant behavioural GET /api/insights + the OperatorDecision writer are gone; the live objective
    # executor stays. Checked at the handler level (robust to Starlette's path-converter rendering).
    assert not hasattr(action_engine, "get_insights")
    assert not hasattr(action_engine, "update_insight_status")
    assert hasattr(action_engine, "execute_insight")


def test_action_engine_compute_path_intact_no_behavioral():
    from routers import action_engine
    assert hasattr(action_engine, "_compute_insights")        # objective path feeds /api/today + Telegram
    assert not hasattr(action_engine, "get_insights")
    assert not hasattr(action_engine, "update_insight_status")
    assert not hasattr(action_engine, "_compute_preference_scores")
    assert not hasattr(action_engine, "_focused_filter")


def test_no_behavioral_tokens_in_production_code():
    patterns = [re.compile(p) for p in _FORBIDDEN]
    offenders = []
    for f in _prod_py_files():
        text = f.read_text(encoding="utf-8")
        for pat in patterns:
            if pat.search(text):
                offenders.append(f"{f.relative_to(_BACKEND)}: {pat.pattern}")
    assert offenders == [], "behavioural references left in production code:\n" + "\n".join(offenders)


# ── objective outcome-learning (DecisionMemory) is preserved unchanged ───────────

def test_decision_memory_and_outcome_ranking_preserved():
    from models.decision_memory import DecisionMemory
    assert DecisionMemory.__tablename__ == "decision_memory"
    # keys / scoping unchanged: context_group is the learning key; per-seller scope joins via Decision
    cols = set(DecisionMemory.__table__.columns.keys())
    assert "context_group" in cols
    from services import outcome_memory_ranking as omr
    assert hasattr(omr, "rank_actions")
    src = (_BACKEND / "services" / "outcome_memory_ranking.py").read_text(encoding="utf-8")
    assert "context_group" in src and "user_id" in src         # scoping intact (not merged across sellers)


# ── identical business data → identical /api/today regardless of any per-user history ──

def _run(c):
    return asyncio.run(c)


async def _mkdb():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    e = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)(), e


class _U:
    def __init__(self, uid):
        self.id = uid


async def _seed_signals(db, uid):
    from models.growth_signal import GrowthSignal
    from models.legal_signal import LegalSignal
    t0 = datetime(2026, 6, 21)
    aid = str(uuid.uuid4())
    db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:ozon:SKU1", marketplace="ozon", sku="SKU1",
           status="active", what="можно поднять цену", why="маржа", meaning="x",
           what_to_do="Поднять цену", expected_effect="маржа", created_at=t0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk",
           insight_key="legal_content_claim_risk:wildberries:SKU1", marketplace="wildberries",
           sku="SKU1", status="active", what="формулировки", why="претензии", meaning="x",
           what_to_do="проверить", expected_effect="риск", created_at=t0))
    await db.commit()


def _shape(resp):
    # comparable projection of the Today feed, independent of the per-user id
    return [(getattr(i, "insight_key", None), getattr(i, "priority_level", None),
             getattr(i, "status", None)) for i in resp.items]


def test_same_business_data_same_today_regardless_of_user():
    async def go():
        from routers.today import get_today
        db, eng = await _mkdb()
        try:
            u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
            await _seed_signals(db, u1)
            await _seed_signals(db, u2)
            r1 = await get_today(contour=None, limit=50, current_user=_U(u1), db=db)
            r2 = await get_today(contour=None, limit=50, current_user=_U(u2), db=db)
            # identical business inputs → identical Today projection (no per-user behavioural divergence)
            assert _shape(r1) == _shape(r2)
            assert len(r1.items) >= 1
        finally:
            await db.close()
            await eng.dispose()          # dispose the async engine (no "Event loop is closed" on teardown)
    _run(go())
