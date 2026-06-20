"""
User autonomy profile (Slice 12: settings + constraints) — tests.

Returns a conservative default profile and provides one-way constraint helpers:
cap_autonomy_level (never raises a scored level) and risk_within_limit. No
execution, no writes, deterministic.
"""
import ast
import asyncio
import inspect
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers all tables
from models.decision import Decision
from services import user_autonomy_profile as prof


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── profile / defaults ───────────────────────────────────────────────────────

def test_default_profile_returned():
    async def go():
        db = await _engine()
        p = await prof.get_user_autonomy_profile(db, str(uuid.uuid4()))
        assert p == {"pricing": "manual", "content": "manual",
                     "analytics": "manual", "max_risk_level": "low"}
    _run(go())


def test_profile_is_a_copy_not_shared():
    async def go():
        db = await _engine()
        p = await prof.get_user_autonomy_profile(db, "u1")
        p["pricing"] = "auto"
        assert prof.DEFAULT_PROFILE["pricing"] == "manual"   # global untouched
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine()
        a = await prof.get_user_autonomy_profile(db, "u1")
        b = await prof.get_user_autonomy_profile(db, "u1")
        assert a == b
    _run(go())


# ── category mapping ─────────────────────────────────────────────────────────

def test_category_mapping():
    assert prof.category_for("set_price") == "pricing"
    assert prof.category_for("update_card") == "content"
    assert prof.category_for("something_else") == "analytics"
    assert prof.category_for(None) == "analytics"


# ── cap_autonomy_level (one-way: only lowers) ────────────────────────────────

def test_manual_caps_to_zero():
    p = {"pricing": "manual", "content": "manual", "analytics": "manual", "max_risk_level": "low"}
    assert prof.cap_autonomy_level(p, "update_card", 2) == 0
    assert prof.cap_autonomy_level(p, "set_price", 1) == 0


def test_suggested_caps_to_one():
    p = {"pricing": "manual", "content": "suggested", "analytics": "manual", "max_risk_level": "low"}
    assert prof.cap_autonomy_level(p, "update_card", 2) == 1   # content suggested → max 1


def test_semi_auto_allows_two():
    p = {"pricing": "manual", "content": "semi_auto", "analytics": "manual", "max_risk_level": "high"}
    assert prof.cap_autonomy_level(p, "update_card", 2) == 2


def test_cap_never_raises_level():
    p = {"pricing": "auto", "content": "auto", "analytics": "auto", "max_risk_level": "high"}
    # scored level 1 stays 1 even though profile would allow 2 (min, never raise)
    assert prof.cap_autonomy_level(p, "update_card", 1) == 1
    assert prof.cap_autonomy_level(p, "set_price", 0) == 0


# ── risk_within_limit ────────────────────────────────────────────────────────

def test_risk_within_limit():
    low = {"max_risk_level": "low"}
    med = {"max_risk_level": "medium"}
    assert prof.risk_within_limit(low, "low") is True
    assert prof.risk_within_limit(low, "medium") is False
    assert prof.risk_within_limit(low, "high") is False
    assert prof.risk_within_limit(med, "medium") is True
    assert prof.risk_within_limit(med, "high") is False


# ── does not override policy ─────────────────────────────────────────────────

def test_does_not_override_policy_block():
    # A policy-blocked item scored to autonomy 0 stays 0 regardless of profile.
    p = {"pricing": "auto", "content": "auto", "analytics": "auto", "max_risk_level": "high"}
    assert prof.cap_autonomy_level(p, "update_card", 0) == 0
    # The profile has no power to "unblock" — it only caps downward.


# ── no writes / no ML guards ─────────────────────────────────────────────────

def test_no_write_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open"))
        await db.commit()

        async def _count():
            return (await db.execute(select(func.count()).select_from(Decision))).scalar()

        before = await _count()
        await prof.get_user_autonomy_profile(db, uid)
        assert await _count() == before
    _run(go())


def test_no_forbidden_imports():
    src = inspect.getsource(prof)
    for forbidden in ("db.add", "db.commit", "db.flush", ".delete(", "session.add"):
        assert forbidden not in src
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "scheduler", "sklearn", "numpy", "torch",
                "insight_decision_bridge", "decision_apply", "close_measurement",
                "execution_measurement_bridge"):
        assert all(bad not in m for m in mods), f"profile must not import {bad}"
