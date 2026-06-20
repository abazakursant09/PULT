"""
Sprint A2.6 — multi-Decision promotion.

Uniqueness is (user_id, insight_key, action_key): a single margin_crisis insight
promotes Decision(set_price) AND Decision(reduce_discount); same insight+action
dedups. Each Decision is independent (own chain, own outcome). Single-action
insights unchanged. Memory append-only intact.
"""
import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from services.insight_decision_bridge import (
    promote_insight_to_decision, promote_insight_alternatives, InsightPromotionDTO,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _dto(itype="margin_crisis", mp="wb", sku="SKU1"):
    return InsightPromotionDTO(insight_key=f"{itype}:{mp}:{sku}", itype=itype,
                               marketplace=mp, sku=sku, problem="p")


async def _decisions(db, uid):
    return (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()


# ── same insight + different action → allowed ────────────────────────────────

def test_same_insight_different_action_allowed():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await promote_insight_to_decision(db, user_id=uid, insight=_dto(), action_key="set_price")
        r2 = await promote_insight_to_decision(db, user_id=uid, insight=_dto(), action_key="reduce_discount")
        await db.commit()
        assert r1.created and r2.created and r1.decision_id != r2.decision_id
        rows = await _decisions(db, uid)
        assert {d.action_key for d in rows} == {"set_price", "reduce_discount"}
    _run(go())


# ── same insight + same action → deduped ─────────────────────────────────────

def test_same_insight_same_action_deduped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        a = await promote_insight_to_decision(db, user_id=uid, insight=_dto(), action_key="set_price")
        b = await promote_insight_to_decision(db, user_id=uid, insight=_dto(), action_key="set_price")
        await db.commit()
        assert a.decision_id == b.decision_id and b.created is False
        assert len(await _decisions(db, uid)) == 1
    _run(go())


# ── promotion creates two decisions (alternatives) ───────────────────────────

def test_promote_alternatives_creates_two():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        results = await promote_insight_alternatives(db, user_id=uid, insight=_dto())
        await db.commit()
        assert len(results) == 3 and all(r.created for r in results)
        rows = await _decisions(db, uid)
        assert {d.action_key for d in rows} == {"set_price", "reduce_discount", "stop_auto_promotion"}
        # independent chains
        assert len({d.decision_chain_id for d in rows}) == 3
    _run(go())


def test_promote_alternatives_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await promote_insight_alternatives(db, user_id=uid, insight=_dto())
        await db.commit()
        again = await promote_insight_alternatives(db, user_id=uid, insight=_dto())
        await db.commit()
        assert all(r.created is False for r in again)
        assert len(await _decisions(db, uid)) == 3
    _run(go())


# ── independent lifecycle / measurement anchors ──────────────────────────────

def test_each_decision_independent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await promote_insight_alternatives(db, user_id=uid, insight=_dto())
        await db.commit()
        rows = await _decisions(db, uid)
        # distinct ids + distinct chains → each opens its own DecisionOutcome later
        assert len({d.id for d in rows}) == 3
        assert all(d.status == "open" and d.step_in_chain == 0 for d in rows)
    _run(go())


# ── single-action insight unchanged ──────────────────────────────────────────

def test_single_action_insight_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        results = await promote_insight_alternatives(db, user_id=uid,
                                                     insight=_dto(itype="seo_opportunity"))
        await db.commit()
        assert len(results) == 1
        rows = await _decisions(db, uid)
        assert [d.action_key for d in rows] == ["update_card"]
    _run(go())


def test_legacy_single_promote_default_action():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto())  # no action_key
        await db.commit()
        rows = await _decisions(db, uid)
        assert len(rows) == 1 and rows[0].action_key == "set_price"  # legacy mapping
    _run(go())


# ── memory append-only intact ────────────────────────────────────────────────

def test_promotion_writes_no_memory():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await promote_insight_alternatives(db, user_id=uid, insight=_dto())
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(DecisionMemory))).scalar()
        assert n == 0  # promotion never writes memory (memory is written on close)
    _run(go())
