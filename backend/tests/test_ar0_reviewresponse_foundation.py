"""AR0 — ReviewResponse lifecycle-foundation schema.

Proves the additive migration: the six new columns exist, existing (legacy, NULL-external) rows
stay valid, duplicate REAL reviews cannot be inserted, NULL-external rows never collide, and
Alembic still has a single head. The migration touches no /sync, /publish or Runtime behaviour —
only schema.
"""
import asyncio
import uuid

from sqlalchemy import select, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db  # noqa: F401
import models  # registers tables
from models.review_response import ReviewResponse

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return e, sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


NEW_COLS = [
    "review_created_at", "safety_category", "manual_required_reason",
    "failure_reason", "publication_attempts", "retry_next_at",
]


def test_new_columns_exist():
    cols = {c.name for c in ReviewResponse.__table__.columns}
    for name in NEW_COLS:
        assert name in cols, f"missing foundation column {name}"


def test_publication_attempts_defaults_to_zero():
    _e, db = _run(_new_db())
    row = ReviewResponse(product_id="p1", status="pending")
    _run(_add(db, row))
    fresh = _run(db.get(ReviewResponse, row.id))
    assert fresh.publication_attempts == 0
    # the other new fields are nullable and default to None
    assert fresh.safety_category is None and fresh.retry_next_at is None


def test_legacy_row_with_null_external_is_valid():
    # A pre-AR0 row carried no external_review_id. It must still insert and load.
    _e, db = _run(_new_db())
    row = ReviewResponse(product_id="p1", status="pending", review_text="legacy", rating=5)
    _run(_add(db, row))
    assert _run(db.get(ReviewResponse, row.id)) is not None


def test_duplicate_real_review_is_rejected():
    _e, db = _run(_new_db())
    _run(_add(db, ReviewResponse(product_id="p1", status="pending",
                                 external_review_id="WB-100", marketplace="wildberries")))
    dup = ReviewResponse(product_id="p1", status="pending",
                         external_review_id="WB-100", marketplace="wildberries")
    raised = False
    try:
        _run(_add(db, dup))
    except IntegrityError:
        raised = True
    assert raised, "the partial unique index did not reject a duplicate real review"


def test_null_external_rows_never_collide():
    # Two rows on the same product with NULL external_review_id must both be allowed —
    # the unique index is partial (WHERE external_review_id IS NOT NULL).
    _e, db = _run(_new_db())
    _run(_add(db, ReviewResponse(product_id="p1", status="pending")))
    _run(_add(db, ReviewResponse(product_id="p1", status="pending")))
    rows = _run(db.execute(select(ReviewResponse).where(ReviewResponse.product_id == "p1"))).scalars().all()
    assert len(rows) == 2


def test_same_external_id_different_marketplace_allowed():
    # Uniqueness is scoped by marketplace: the same external id on two marketplaces is distinct.
    _e, db = _run(_new_db())
    _run(_add(db, ReviewResponse(product_id="p1", status="pending",
                                 external_review_id="X-1", marketplace="wildberries")))
    _run(_add(db, ReviewResponse(product_id="p1", status="pending",
                                 external_review_id="X-1", marketplace="ozon")))
    rows = _run(db.execute(select(ReviewResponse))).scalars().all()
    assert len(rows) == 2


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["plp1a2b3c4d01"], heads


async def _add(db, obj):
    db.add(obj)
    await db.commit()
