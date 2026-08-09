"""SECURITY-2D-2-A — atomic promo activation cap (INT-1/INT-2) — SQLite unit + source guards.

Functional smoke on in-memory SQLite (single-threaded, so no true race here — the concurrency proof is
in test_promo_atomic_cap_pg.py on real PostgreSQL). These lock the response contract, the same-user
duplicate 400, the cap 400, the unlimited path, and the source-level invariants (no Python read-modify-
write, a guarded UPDATE with the cap predicate + RETURNING, exactly one commit, models/migrations
untouched, no secret in logs).
"""
from __future__ import annotations

import ast
import asyncio
import os
import types
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 — register mappers
from database import Base
from models.promo_code import PromoCode, PromoCodeActivation
import routers.promo as promo

_HERE = os.path.dirname(__file__)
_PROMO_SRC = os.path.join(_HERE, "..", "routers", "promo.py")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _mk():
    e = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return e, sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


async def _seed_promo(Session, *, code, max_activations, current=0):
    async with Session() as s:
        s.add(PromoCode(id=str(uuid.uuid4()), code=code, type="percent", value=10.0,
                        applicable_plans="all", max_activations=max_activations,
                        current_activations=current, is_active=True))
        await s.commit()


async def _apply(Session, uid, code):
    async with Session() as s:
        return await promo.apply_promo(promo.ApplyRequest(code=code, plan="master"),
                                       db=s, current_user=types.SimpleNamespace(id=uid))


async def _counter(Session, code):
    from sqlalchemy import select
    async with Session() as s:
        p = (await s.execute(select(PromoCode).where(PromoCode.code == code))).scalars().first()
        return p.current_activations


async def _activation_count(Session, code):
    from sqlalchemy import select, func
    async with Session() as s:
        pid = (await s.execute(select(PromoCode.id).where(PromoCode.code == code))).scalar_one()
        return (await s.execute(
            select(func.count()).select_from(PromoCodeActivation)
            .where(PromoCodeActivation.promo_id == pid))).scalar_one()


# ── functional (single-threaded) ─────────────────────────────────────────────────

def test_normal_apply_records_one_and_increments():
    async def go():
        e, S = await _mk()
        try:
            await _seed_promo(S, code="OK", max_activations=5)
            r = await _apply(S, "u1", "OK")
            assert r["ok"] is True and r["discount_amount"] == 99.0   # 10% of 990
            assert await _activation_count(S, "OK") == 1
            assert await _counter(S, "OK") == 1
        finally:
            await e.dispose()
    _run(go())


def test_same_user_second_apply_is_400_not_500():
    async def go():
        e, S = await _mk()
        try:
            await _seed_promo(S, code="DUP", max_activations=5)
            await _apply(S, "u1", "DUP")
            with pytest.raises(promo.HTTPException) as ei:
                await _apply(S, "u1", "DUP")
            assert ei.value.status_code == 400
            assert await _activation_count(S, "DUP") == 1     # retry did NOT add a second
            assert await _counter(S, "DUP") == 1              # nor increment again
        finally:
            await e.dispose()
    _run(go())


def test_cap_full_rejected_and_nothing_persists():
    async def go():
        e, S = await _mk()
        try:
            await _seed_promo(S, code="FULL", max_activations=1, current=1)   # already at cap
            with pytest.raises(promo.HTTPException) as ei:
                await _apply(S, "late", "FULL")
            assert ei.value.status_code == 400
            assert await _activation_count(S, "FULL") == 0    # loser's activation rolled back
            assert await _counter(S, "FULL") == 1             # counter never exceeded the cap
        finally:
            await e.dispose()
    _run(go())


def test_unlimited_accepts_many_distinct_users():
    async def go():
        e, S = await _mk()
        try:
            await _seed_promo(S, code="INF", max_activations=None)
            for uid in ("a", "b", "c"):
                await _apply(S, uid, "INF")
            assert await _activation_count(S, "INF") == 3
            assert await _counter(S, "INF") == 3
        finally:
            await e.dispose()
    _run(go())


# ── source / AST guards ───────────────────────────────────────────────────────────

def _src():
    with open(_PROMO_SRC, encoding="utf-8") as f:
        return f.read()


def test_no_python_read_modify_write_on_counter():
    src = _src()
    # the old Python read-modify-write must be gone; the ONLY increment is the atomic SQL form,
    # which appears exactly once (inside _CAP_RESERVE) — never as an ORM attribute mutation.
    assert "current_activations += 1" not in src
    assert ".current_activations +" not in src
    assert src.count("current_activations = current_activations + 1") == 1   # the guarded SQL, once


def test_guarded_update_has_cap_predicate_and_returning():
    src = _src()
    # the reservation SQL must be the guarded, atomic form
    assert "UPDATE promo_codes SET current_activations = current_activations + 1" in src
    assert "current_activations < max_activations" in src
    assert "max_activations IS NULL" in src
    assert "RETURNING id, current_activations" in src


def test_exactly_one_commit_in_apply_path():
    src = _src()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "apply_promo")
    seg = ast.get_source_segment(src, fn)
    assert seg.count("await db.commit()") == 1


def test_no_secret_in_promo_logs():
    src = _src()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("warning", "info", "error", "exception", "debug"):
            for a in node.args:
                txt = ast.get_source_segment(src, a) or ""
                for bad in ("code", "email", "exc", "current_user", "body.", "%s"):
                    assert bad not in txt, f"log call may leak: {txt}"
