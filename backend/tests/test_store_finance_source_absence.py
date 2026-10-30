"""No hidden CSV fallback, fake CSV zero, or source mixing in a store total."""
import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from config import settings
from database import Base
from models.api_sync_state import ApiSyncState
from models.imported_finance import ImportedFinanceRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.store_data_source_policy import StoreDataSourcePolicy
from models.user import User
from models.workspace import Workspace
from routers.store_catalog import store_finance_summary


async def seed(db):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@example.invalid", name="Synthetic", hashed_password="x", is_verified=True)
    workspace = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id)
    account = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=workspace.id, marketplace="wildberries",
                                 identity_status="verified", external_account_id=str(uuid.uuid4()))
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=account.id, marketplace="wildberries",
                             store_key="primary", label="Synthetic", source="manual", status="active")
    db.add_all([user, workspace, account, store])
    await db.commit()
    return user, account, store


async def calculate(*, pref="auto", csv=(), api=(), returns=(), coverage="covered", extra_store=False, foreign=False):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
            user, account, store = await seed(db)
            for number in csv:
                db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="synthetic", user_id=user.id, marketplace="wb",
                    date="2026-07-10", sku="fixture", revenue=number, net_profit=number * .3, source="csv",
                    marketplace_account_id=account.id, marketplace_store_id=store.id, link_status="linked"))
            for number in api:
                db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=account.id,
                    marketplace_store_id=store.id, marketplace="wildberries", source="api",
                    external_operation_id=str(uuid.uuid4()), operation_type="sale",
                    provider_dataset="finance", amount=Decimal(str(number)), occurred_at=datetime(2026,7,10), fetched_at=datetime(2026,7,11)))
            # Signed sale corrections belong to revenue. Return operations stay
            # separate under the existing authority map; never reclassify them.
            for number in returns:
                db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=account.id,
                    marketplace_store_id=store.id, marketplace="wildberries", source="api",
                    external_operation_id=str(uuid.uuid4()), operation_type="return", provider_dataset="finance",
                    amount=Decimal(str(number)), occurred_at=datetime(2026,7,10), fetched_at=datetime(2026,7,11)))
            if coverage is not None:
                db.add(ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                    marketplace_account_id=account.id, marketplace_store_id=store.id, data_type="finance",
                    status="failed" if coverage == "failed" else "synced", coverage_complete=coverage != "incomplete",
                    skipped_rows_count=1 if coverage == "skipped" else 0, covered_from="2026-07-01",
                    covered_to="2026-07-31", last_success_at=datetime(2026,7,11)))
            db.add(StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id, metric_type="revenue", preference=pref))
            if extra_store:
                other_account = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=account.workspace_id,
                    marketplace="wildberries", identity_status="verified", external_account_id=str(uuid.uuid4()))
                other = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=other_account.id, marketplace="wildberries",
                    store_key="primary", label="Other", source="manual", status="active")
                db.add_all([other_account, other])
                db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id="other", user_id=user.id, marketplace="wb", date="2026-07-10",
                    sku="other", revenue=999, net_profit=999, source="csv", marketplace_account_id=other_account.id,
                    marketplace_store_id=other.id, link_status="linked"))
                db.add(MarketplaceOperation(id=str(uuid.uuid4()), marketplace_account_id=other_account.id,
                    marketplace_store_id=other.id, marketplace="wildberries", source="api", external_operation_id=str(uuid.uuid4()),
                    operation_type="sale", provider_dataset="finance", amount=Decimal("999"),
                    occurred_at=datetime(2026,7,10), fetched_at=datetime(2026,7,11)))
            await db.commit()
            if foreign:
                other_user, _, _ = await seed(db)
                with pytest.raises(HTTPException) as exc:
                    await store_finance_summary(store.id, db=db, user=other_user)
                assert exc.value.status_code == 404
                return None
            return await store_finance_summary(store.id, db=db, user=user)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", True)


@pytest.mark.parametrize("coverage", [None, "failed", "incomplete", "skipped"])
def test_only_api_without_coverage_does_not_relabel_csv_as_selected_money(coverage):
    out = asyncio.run(calculate(pref="api", csv=(100,), api=(250,), coverage=coverage))
    assert out.revenue is None and out.net_profit is None and out.source is None
    assert out.completeness == "no_data" and "revenue" in out.missing_fields
    assert not out.conflict and out.conflict_candidates is None
    assert out.unassigned_revenue == 100  # independent known CSV reconciliation is not lost


@pytest.mark.parametrize("pref", ["csv", "api", "auto"])
def test_absence_of_both_sources_is_not_real_zero(pref):
    out = asyncio.run(calculate(pref=pref, coverage=None))
    assert out.revenue is None and out.net_profit is None and out.source is None
    assert out.completeness == "no_data" and not out.conflict


def test_only_api_with_coverage_but_no_api_rows_does_not_use_csv():
    out = asyncio.run(calculate(pref="api",csv=(100,),coverage="covered"))
    assert out.revenue is None and out.net_profit is None and out.source is None
    assert out.completeness == "no_data" and out.unassigned_revenue == 100


def test_only_csv_with_no_csv_rows_does_not_invent_zero_from_available_api():
    out = asyncio.run(calculate(pref="csv",api=(250,)))
    assert out.revenue is None and out.net_profit is None and out.source is None
    assert out.completeness == "no_data" and not out.conflict


@pytest.mark.parametrize("api,expected", [((250,),250), ((250,-50),200), ((250,-250),0)])
def test_auto_api_only_has_no_fake_csv_candidate(api, expected):
    out = asyncio.run(calculate(api=api))
    assert out.source == "api" and out.revenue == expected and out.net_profit is None
    assert out.completeness == "incomplete" and out.missing_fields == ["cogs"]
    assert not out.conflict and out.conflict_candidates is None


@pytest.mark.parametrize("csv,expected", [((0,),0), ((-50,),-50), ((100,-100),0)])
def test_real_csv_zero_negative_and_cancelled_returns_remain_real_candidates(csv, expected):
    out = asyncio.run(calculate(csv=csv, api=(250,)))
    assert out.source == "csv" and out.revenue == expected and out.conflict
    assert out.conflict_candidates.csv == expected
    assert out.net_profit == expected*.3


@pytest.mark.parametrize("pref,coverage,expected_source,expected", [
    ("csv","covered","csv",100), ("api","covered","api",250), ("auto","incomplete","csv",100),
])
def test_known_source_controls(pref,coverage,expected_source,expected):
    out = asyncio.run(calculate(pref=pref,csv=(100,),api=(250,),coverage=coverage))
    assert out.source == expected_source and out.revenue == expected and not out.conflict


def test_other_store_rows_do_not_change_this_store_candidates():
    out = asyncio.run(calculate(csv=(100,),api=(100,),extra_store=True))
    assert out.source == "api" and out.revenue == 100 and not out.conflict


def test_finance_return_operations_do_not_change_revenue_authority():
    out = asyncio.run(calculate(csv=(100,),api=(100,),returns=(-50,)))
    assert out.source == "api" and out.revenue == 100 and not out.conflict


def test_foreign_store_remains_same_404():
    asyncio.run(calculate(csv=(100,),foreign=True))


@pytest.mark.parametrize("csv,expected", [((),0),((100,),100)])
def test_flag_off_keeps_legacy_csv_exactly(monkeypatch,csv,expected):
    monkeypatch.setattr(settings, "api_data_sync_enabled", False)
    out = asyncio.run(calculate(pref="api",csv=csv,api=(250,),coverage=None))
    assert out.source == "csv" and out.revenue == expected and out.completeness == "complete"
    assert out.net_profit == expected*.3 and not out.conflict
