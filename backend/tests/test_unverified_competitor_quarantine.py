"""Legacy observations have no provenance and cannot authorize price decisions."""
import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models.competitor_analysis import CompetitorAnalysis
from models.pricing_rule import PriceChangeLog, PricingRule
from models.product import Product
from models.user import User
from routers import pricing
from services.marketplace import insight_mapping
from tasks import check_pricing as task


class UntrustedObservation:
    @property
    def price(self):
        raise AssertionError("unverified price was inspected")


@pytest.mark.parametrize("rows", [[], [UntrustedObservation()], [UntrustedObservation()] * 3])
def test_legacy_recommendation_never_inspects_or_recommends_unverified_rows(rows):
    rule = PricingRule(target_position="equal_top_1", target_percent=0,
                       min_price=1, max_price=99999, reaction_threshold=0)
    market, recommended, reason, deviation, should_change = task.compute_recommendation(rows, rule, 1000)
    assert market is None and recommended is None
    assert "подтвержд" in reason
    assert deviation == 0 and should_change is False


async def _scenario(check):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            owner = User(id=str(uuid.uuid4()), email="synthetic@example.invalid", name="Seller",
                         hashed_password="synthetic-not-a-real-password")
            product = Product(id=str(uuid.uuid4()), user_id=owner.id, name="Stored product",
                              marketplace="wildberries", sku="synthetic-sku", price=1000)
            db.add_all([owner, product])
            await db.flush()
            rule = PricingRule(product_id=product.id, target_position="equal_top_1", target_percent=0,
                               min_price=1, max_price=99999, auto_mode=True, reaction_threshold=0)
            row = CompetitorAnalysis(product_id=product.id, competitor_name="Unknown provenance",
                                     marketplace="wildberries", price=2, significance="primary")
            db.add_all([rule, row])
            await db.commit()
            row_id = row.id

            legacy_reads = []

            def forbid_legacy_read(_conn, _cursor, statement, _parameters, _context, _many):
                if "competitor_analysis" in statement.lower():
                    legacy_reads.append(statement)
                    raise AssertionError("decision path accessed unverified legacy records")

            event.listen(engine.sync_engine, "before_cursor_execute", forbid_legacy_read)
            try:
                await check(db, factory, owner, product)
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", forbid_legacy_read)
            assert legacy_reads == []  # Also catches attempts swallowed by background-task logging.
            await db.refresh(product)
            assert product.price == 1000
            assert (await db.get(CompetitorAnalysis, row_id)).price == 2
            assert (await db.execute(select(func.count()).select_from(CompetitorAnalysis))).scalar_one() == 1
            assert (await db.execute(select(func.count()).select_from(PriceChangeLog))).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("handler", ["check_price", "apply_price"])
def test_price_endpoints_report_missing_verified_data_without_reads_or_execution(handler, monkeypatch):
    execute = AsyncMock(side_effect=AssertionError("unverified observations reached executor"))
    monkeypatch.setattr(pricing.executor, "execute", execute)

    async def check(db, _factory, owner, product):
        kwargs = {"product_id": product.id, "current_user": owner, "db": db}
        if handler == "apply_price":
            kwargs["idempotency_key"] = str(uuid.uuid4())
        with pytest.raises(HTTPException) as exc:
            await getattr(pricing, handler)(**kwargs)
        assert exc.value.status_code == 422
        assert "подтвержд" in str(exc.value.detail)

    asyncio.run(_scenario(check))
    execute.assert_not_awaited()


def test_scheduled_legacy_pricing_cannot_change_local_price_or_history(monkeypatch):
    async def check(_db, factory, _owner, product):
        monkeypatch.setattr(task, "AsyncSessionLocal", factory)
        await task.check_pricing(product.id)
    asyncio.run(_scenario(check))


@pytest.mark.parametrize("explicit_price", [None, 1234.0])
def test_insight_requires_explicit_price_and_preserves_manual_plan(explicit_price):
    async def check(db, _factory, owner, product):
        override = {} if explicit_price is None else {"price": explicit_price}
        plan = await insight_mapping.resolve_plan(db, owner.id, "margin_crisis:wildberries:synthetic-sku", override)
        if explicit_price is None:
            assert not plan.ready and plan.needs_input == ["price"]
            assert not plan.automation_eligible and not plan.payload
            assert "подтвержд" in plan.descriptor.get("reason", "")
        else:
            assert plan.ready and plan.action_type == "set_price"
            assert plan.payload["price"] == explicit_price
            assert plan.payload["offer_id"] == product.sku
    asyncio.run(_scenario(check))


@pytest.mark.parametrize("handler", ["check_price", "apply_price"])
def test_price_endpoint_ownership_still_fails_before_missing_data(handler):
    async def check(db, _factory, _owner, product):
        stranger = User(id=str(uuid.uuid4()))
        kwargs = {"product_id": product.id, "current_user": stranger, "db": db}
        if handler == "apply_price":
            kwargs["idempotency_key"] = str(uuid.uuid4())
        with pytest.raises(HTTPException) as exc:
            await getattr(pricing, handler)(**kwargs)
        assert exc.value.status_code == 404
    asyncio.run(_scenario(check))
