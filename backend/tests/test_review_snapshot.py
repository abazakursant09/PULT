"""
Review A3 — ReviewSnapshot contract + safety policy tests.

Snapshot built from ReviewResponse; missing review → ReviewDataUnavailable; safety
policy deterministic (SAFE allows auto; ATTENTION/RISK do not; RISK default
manual_only; 1-2 stars always RISK; 5 stars no text SAFE); marketplace agnostic;
core imports no MP clients and no AI/generation modules.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.review_response import ReviewResponse
from models.product import Product

from services.review.snapshot import ReviewSnapshot, ReviewDataUnavailable
from services.review import internal_source
from services.review.internal_source import build_snapshot_from_reviews
from services.review.safety_policy import classify_safety, SAFE, ATTENTION, RISK, AUTO, MANUAL_ONLY


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_review(db, *, marketplace="wb", rating=5, text=None, response=None, status="pending"):
    uid = str(uuid.uuid4())
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="Чайник", marketplace=marketplace,
                   category="Кухня", sku="SKU1")
    db.add(prod)
    rr = ReviewResponse(id=str(uuid.uuid4()), product_id=prod.id, review_text=text, rating=rating,
                        response_text=response, status=status, marketplace=marketplace)
    db.add(rr); await db.flush()
    return rr.id


# ── 1. snapshot from internal source ─────────────────────────────────────────

def test_snapshot_from_reviews():
    async def go():
        db = await _engine()
        rid = await _seed_review(db, rating=5, text="Отличный чайник")
        snap = await build_snapshot_from_reviews(db, review_id=rid)
        assert isinstance(snap, ReviewSnapshot)
        assert snap.rating == 5 and snap.has_text is True and snap.source == "reviews"
        assert snap.product_name == "Чайник" and snap.category == "Кухня" and snap.sku == "SKU1"
        assert snap.safety_category == "SAFE"
        assert snap.field_availability["brand"] is False   # not on legacy Product
    _run(go())


# ── 2. missing review → ReviewDataUnavailable ────────────────────────────────

def test_missing_review_unavailable():
    async def go():
        db = await _engine()
        assert (await build_snapshot_from_reviews(db, review_id="nope")).reason == "review_missing"
        assert (await build_snapshot_from_reviews(None, review_id="x")).reason == "no_db_context"
        assert (await build_snapshot_from_reviews(db, review_id="")).reason == "insufficient_data"
    _run(go())


# ── 3. safety policy: SAFE/ATTENTION/RISK + AUTO rules ───────────────────────

def test_safety_policy():
    safe = classify_safety(5, False, None)
    assert safe.category == SAFE and AUTO in safe.allowed_modes
    att = classify_safety(3, False, None)
    assert att.category == ATTENTION and AUTO not in att.allowed_modes
    risk = classify_safety(1, False, None)
    assert risk.category == RISK and AUTO not in risk.allowed_modes and risk.default_mode == MANUAL_ONLY


# ── 4. 1-2 stars always RISK ─────────────────────────────────────────────────

def test_low_stars_always_risk():
    for r in (1, 2):
        d = classify_safety(r, True, "нормально")
        assert d.category == RISK and AUTO not in d.allowed_modes
    # hard complaint marker forces RISK even on high stars
    assert classify_safety(5, True, "пришёл брак, оформляю возврат").category == RISK


# ── 5. 5 stars without text SAFE ─────────────────────────────────────────────

def test_five_stars_no_text_safe():
    d = classify_safety(5, False, None)
    assert d.category == SAFE and AUTO in d.allowed_modes


# ── 6. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine()
            rid = await _seed_review(db, marketplace=mp, rating=1, text="ужас")
            snap = await build_snapshot_from_reviews(db, review_id=rid)
            assert isinstance(snap, ReviewSnapshot)
            assert snap.marketplace == mp and snap.safety_category == "RISK"
    _run(go())


# ── 7. core imports no marketplace clients ───────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(internal_source)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
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


# ── 8. no AI / reply-generation modules or symbols ───────────────────────────

def test_no_ai_or_generation():
    core_dir = Path(inspect.getfile(internal_source)).parent
    for forbidden in ("generator.py", "llm.py", "reply_generator.py", "rules.py", "engine.py"):
        assert not (core_dir / forbidden).exists(), f"{forbidden} not in A3"
    for path in core_dir.rglob("*.py"):
        src = path.read_text(encoding="utf-8").lower()
        for bad in ("openai", "anthropic", "llm", "generate_reply", "gpt"):
            assert bad not in src, f"{bad} in {path.name}"
