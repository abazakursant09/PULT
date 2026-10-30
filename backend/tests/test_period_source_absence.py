"""Period totals must preserve source absence and include owned API-only stores."""
import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base
from models.api_sync_state import ApiSyncState
from models.imported_finance import ImportedFinanceRow
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.store_data_source_policy import StoreDataSourcePolicy
from services.finance_aggregator import FinancePeriod, _agg_period, _delta_pct, _has_any_data
from services.source_policy.store_totals import resolved_finance_period
from test_store_finance_source_absence import seed


async def scenario(*, csv=None, api=None, coverage=True, pref="api", foreign=False, wrapped=False,
                   presence=False, other_csv=None, api_day=datetime(2026, 7, 10)):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
            user, account, store = await seed(db)
            if csv is not None:
                db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="synthetic", user_id=user.id,
                    marketplace="wb", date="2026-07-10", sku="fixture", revenue=csv,
                    net_profit=csv * .3, source="csv", marketplace_account_id=account.id,
                    marketplace_store_id=store.id, link_status="linked"))
            if api is not None:
                db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=account.id,
                    marketplace_store_id=store.id, marketplace="wildberries", source="api",
                    external_operation_id=str(uuid.uuid4()), operation_type="sale",
                    provider_dataset="finance", amount=Decimal(str(api)), occurred_at=api_day))
            if coverage:
                db.add(ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                    marketplace_account_id=account.id, marketplace_store_id=store.id, data_type="finance",
                    status="synced", coverage_complete=True, skipped_rows_count=0,
                    covered_from="2026-07-01", covered_to="2026-07-31", last_success_at=datetime(2026, 7, 11)))
            db.add(StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id,
                metric_type="revenue", preference=pref))
            if other_csv is not None:
                account2 = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=account.workspace_id,
                    marketplace="wildberries", identity_status="verified", external_account_id=str(uuid.uuid4()))
                store2 = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=account2.id,
                    marketplace="wildberries", store_key="primary", label="Second", source="manual", status="active")
                db.add_all([account2, store2])
                db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="synthetic", user_id=user.id,
                    marketplace="wb", date="2026-07-10", sku="second", revenue=other_csv,
                    net_profit=other_csv * .3, source="csv", marketplace_account_id=account2.id,
                    marketplace_store_id=store2.id, link_status="linked"))
            if foreign:
                user, _, _ = await seed(db)
            await db.commit()
            if presence:
                return await _has_any_data(user.id, db)
            calculate = _agg_period if wrapped else resolved_finance_period
            if wrapped:
                return await calculate(user.id, "2026-07-01", "2026-07-31", db)
            return await calculate(db, user.id, "2026-07-01", "2026-07-31")
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def api_enabled(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", True)


@pytest.mark.parametrize("value", [250, 0, -50])
def test_api_only_store_is_included_without_fake_csv_candidate(value):
    total = asyncio.run(scenario(api=value))
    assert total.revenue == value and total.revenue_source == "api"
    assert total.net_profit is None and total.completeness == "incomplete"
    assert not total.conflict


def test_unavailable_selected_source_does_not_claim_csv_profit():
    total = asyncio.run(scenario(csv=100, api=250, coverage=False))
    assert total.revenue is None and total.net_profit is None
    assert total.completeness == "no_data" and "revenue" in total.missing_fields


def test_wrapper_preserves_unknown_profit_and_margin():
    total = asyncio.run(scenario(csv=100, api=250, wrapped=True))
    assert total.revenue == 250
    assert total.profit is None and total.effective_profit is None and total.margin_pct is None
    assert total.drr_pct is None


def test_wrapper_preserves_absence_instead_of_calculated_fallback():
    total = asyncio.run(scenario(csv=100, api=250, coverage=False, wrapped=True))
    assert total.revenue is None and total.profit is None and total.source is None
    assert total.effective_profit is None and total.margin_pct is None


def test_foreign_api_store_never_enters_total():
    total = asyncio.run(scenario(api=999, foreign=True))
    assert total.revenue in (None, 0) and total.net_profit in (None, 0)


def test_flag_off_keeps_csv_compatibility(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", False)
    total = asyncio.run(scenario(csv=100, api=250, coverage=False, wrapped=True))
    assert total.revenue == 100 and total.profit == 30 and total.source == "csv"


def test_real_csv_zero_is_not_replaced_by_profit_fallback():
    total = asyncio.run(scenario(csv=0, pref="csv", wrapped=True))
    assert total.has_data and total.profit == 0 and total.effective_profit == 0


def test_api_only_evidence_is_not_treated_as_demo():
    assert asyncio.run(scenario(api=250, presence=True)) is True
    assert asyncio.run(scenario(api=250, presence=True, foreign=True)) is False


def test_unknown_deltas_do_not_invent_growth():
    assert _delta_pct(None, 100) is None
    assert _delta_pct(100, None) is None
    assert _delta_pct(0, 100) == -100


def test_existing_legacy_profit_fallback_remains_available():
    assert FinancePeriod(revenue=100, profit=0, commission=10).effective_profit == 90
    assert FinancePeriod(revenue=100, profit=0, commission=10, profit_resolved=True).effective_profit == 0


def test_mixed_stores_sum_each_source_once_and_keep_profit_unknown():
    total = asyncio.run(scenario(csv=100, api=250, other_csv=40))
    assert total.revenue == 290 and total.revenue_source == "mixed"
    assert total.net_profit is None and total.completeness == "incomplete"


def test_partial_known_total_is_not_presented_as_complete_money():
    total = asyncio.run(scenario(csv=100, api=250, coverage=False, other_csv=40))
    assert total.revenue is None and total.net_profit is None
    assert total.completeness == "incomplete" and total.has_values


def test_other_period_api_does_not_enter_requested_period():
    total = asyncio.run(scenario(api=999, api_day=datetime(2026, 8, 1)))
    assert total.revenue is None and total.completeness == "no_data"


def test_today_contract_accepts_unknown_money_without_zero_conversion():
    from routers.today import TodaySummaryView
    value = TodaySummaryView(revenue_today=None, profit_today=None, margin_pct=None,
        delta_revenue_pct=None, critical_count=0, loss_products_count=0,
        growth_opportunities_count=0, low_stock_count=0, is_demo=False, has_data=True)
    assert value.model_dump()["profit_today"] is None
    assert value.model_dump()["revenue_today"] is None


def test_notification_formatter_keeps_unknown_distinct_from_zero():
    from tasks.scheduler import _fk
    assert _fk(None) == "—"
    assert _fk(0) == "0"
    assert _fk(-50) == "-50"
