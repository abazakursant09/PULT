"""A saved draft is not proof that a response reached the marketplace."""
import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.product import Product
from models.review_response import ReviewResponse
from services.review.engine import evaluate_snapshot
from services.review.evaluation import RuleResult
from services.review.internal_source import build_snapshot_from_reviews


@pytest.mark.parametrize("status", ["pending", "drafted", "approved"])
@pytest.mark.parametrize("rating,problem", [(1, "unanswered_negative_review"), (3, "unanswered_attention_review"),
                                          (5, "safe_review_can_reply")])
def test_saved_unpublished_reply_does_not_suppress_unanswered_review(status, rating, problem):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
                uid, pid, rid = [str(uuid.uuid4()) for _ in range(3)]
                db.add(Product(id=pid, user_id=uid, name="Synthetic product", sku="SYNTHETIC", marketplace="ozon"))
                db.add(ReviewResponse(id=rid, product_id=pid, rating=rating, review_text="Synthetic review",
                                      response_text="Saved draft, not published", status=status, marketplace="ozon"))
                await db.commit()
                before = await db.get(ReviewResponse, rid)
                snapshot = await build_snapshot_from_reviews(db, review_id=rid, owner_user_id=uid)
                assert snapshot.answered is False
                assert snapshot.answer_text == "Saved draft, not published"
                results = {r.problem_type: r for r in evaluate_snapshot(snapshot)}
                assert results["already_answered"].result == RuleResult.NOT_TRIGGERED
                assert results[problem].result == RuleResult.TRIGGERED
                await db.refresh(before)
                assert before.status == status and before.published_at is None
                assert before.response_text == "Saved draft, not published"
        finally:
            await engine.dispose()
    asyncio.run(go())


@pytest.mark.parametrize("response", [None, "", "Published response"])
def test_published_status_remains_answered_even_without_local_reply_text(response):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
                uid, pid, rid = [str(uuid.uuid4()) for _ in range(3)]
                db.add(Product(id=pid, user_id=uid, name="Synthetic product", marketplace="ozon"))
                db.add(ReviewResponse(id=rid, product_id=pid, rating=1, response_text=response,
                                      status="published", marketplace="ozon"))
                await db.commit()
                snapshot = await build_snapshot_from_reviews(db, review_id=rid, owner_user_id=uid)
                assert snapshot.answered is True
                results = {r.problem_type: r for r in evaluate_snapshot(snapshot)}
                assert results["already_answered"].result == RuleResult.TRIGGERED
                assert results["unanswered_negative_review"].result == RuleResult.NOT_TRIGGERED
        finally:
            await engine.dispose()
    asyncio.run(go())
