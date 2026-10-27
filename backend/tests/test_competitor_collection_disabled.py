"""Legacy random competitors must neither be generated nor presented as evidence."""
import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base, get_db
from dependencies import get_current_user
from models.competitor_analysis import CompetitorAnalysis
from models.product import Product
from models.user import User
from routers import products
from schemas.product import ProductCreate
from tasks import collect_competitors as collector


@pytest.mark.parametrize("method,suffix", [
    ("GET", "competitors"), ("GET", "report"), ("POST", "competitors/refresh"),
])
@pytest.mark.parametrize("identifier", ["00000000-0000-4000-8000-000000000001", "invalid-id"])
def test_legacy_routes_are_neutral_404_before_auth_or_database(method, suffix, identifier):
    app = FastAPI()
    app.include_router(products.router, prefix="/api/products")
    touched = []

    async def forbidden_auth():
        touched.append("auth")
        raise AssertionError("disabled route reached auth")

    async def forbidden_db():
        touched.append("db")
        raise AssertionError("disabled route reached DB")
        yield  # pragma: no cover

    app.dependency_overrides[get_current_user] = forbidden_auth
    app.dependency_overrides[get_db] = forbidden_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, f"/api/products/{identifier}/{suffix}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert touched == []


@pytest.mark.parametrize("name", ["get_competitors", "get_report", "refresh_competitors"])
def test_direct_legacy_handlers_also_refuse_without_side_effects(name):
    db = Mock()
    db.execute = AsyncMock(side_effect=AssertionError("must not read old rows"))
    background = BackgroundTasks()
    kwargs = {"product_id": uuid.uuid4(), "current_user": Mock(), "db": db}
    if name == "refresh_competitors":
        kwargs["background_tasks"] = background
    with pytest.raises(HTTPException) as exc:
        asyncio.run(getattr(products, name)(**kwargs))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"
    db.execute.assert_not_called()
    assert background.tasks == []


def test_create_product_does_not_schedule_synthetic_collection():
    db = Mock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    background = BackgroundTasks()
    seller = Mock(id=str(uuid.uuid4()))
    data = ProductCreate(name="Synthetic test card", marketplace="wildberries", price=321)
    result = asyncio.run(products.create_product(data, background, seller, db))
    assert isinstance(result, Product)
    assert result.user_id == seller.id and result.price == 321
    db.add.assert_called_once_with(result)
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(result)
    assert background.tasks == []


def test_main_app_mount_keeps_all_three_legacy_routes_closed():
    from main import app
    expected = {"/api/products/{product_id}/competitors", "/api/products/{product_id}/report",
                "/api/products/{product_id}/competitors/refresh"}
    found = set()
    for route in app.routes:
        if route.path in expected:
            found.add(route.path)
            assert products._competitor_data_unavailable in {
                dependency.call for dependency in route.dependant.dependencies}
    assert found == expected


def test_product_http_contract_remains_201_without_a_background_collector(monkeypatch):
    app = FastAPI()
    app.include_router(products.router, prefix="/api/products")
    seller = Mock(id=str(uuid.uuid4()))
    db = Mock()
    db.commit = AsyncMock()

    async def refresh(product):
        product.id = str(uuid.uuid4())
        product.created_at = datetime(2020, 1, 1)

    db.refresh = AsyncMock(side_effect=refresh)

    async def actor():
        return seller

    async def session():
        yield db

    fake_collector = AsyncMock()
    monkeypatch.setattr(products, "collect_competitors", fake_collector, raising=False)
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        response = client.post("/api/products", json={"name": "Test card", "marketplace": "ozon", "price": 321})
    assert response.status_code == 201
    assert response.json()["user_id"] == seller.id
    assert response.json()["price"] == 321
    db.commit.assert_awaited_once()
    fake_collector.assert_not_awaited()


def test_previously_queued_collector_is_inert_even_when_called_directly(monkeypatch):
    trap = Mock(side_effect=AssertionError("no generation, delay, DB or deletion"))
    monkeypatch.setattr(collector, "AsyncSessionLocal", trap, raising=False)
    monkeypatch.setattr(collector, "_make_competitor", trap, raising=False)
    if hasattr(collector, "asyncio"):
        monkeypatch.setattr(collector.asyncio, "sleep", AsyncMock(side_effect=trap.side_effect))
    assert asyncio.run(collector.collect_competitors(str(uuid.uuid4()), "wildberries")) is None
    trap.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_ordinary_product_routes_keep_authentication(method):
    app = FastAPI()
    app.include_router(products.router, prefix="/api/products")

    async def unauthorized():
        raise HTTPException(401, "Authentication required")

    app.dependency_overrides[get_current_user] = unauthorized
    with TestClient(app) as client:
        response = client.request(method, "/api/products", json={"name": "X", "marketplace": "ozon"})
    assert response.status_code == 401


def test_real_product_creation_ownership_and_existing_rows_are_preserved():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with maker() as db:
                seller = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@example.com",
                              name="Test seller", hashed_password="synthetic-not-a-real-password")
                other = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@example.com",
                             name="Other", hashed_password="synthetic-not-a-real-password")
                db.add_all([seller, other])
                await db.flush()
                existing = Product(user_id=seller.id, name="Existing", marketplace="ozon", price=111)
                foreign = Product(user_id=other.id, name="Foreign", marketplace="ozon", price=222)
                db.add_all([existing, foreign])
                await db.flush()
                retained = CompetitorAnalysis(product_id=existing.id, competitor_name="Unverified legacy row",
                                              marketplace="ozon", price=99, significance="direct",
                                              collected_at=datetime(2020, 1, 1))
                db.add(retained)
                await db.commit()
                row_id = retained.id
                created = await products.create_product(ProductCreate(name="New", marketplace="ozon", price=333),
                                                        BackgroundTasks(), seller, db)
                assert created.id and created.price == 333
                owned = await products.list_products(seller, db)
                assert {p.id for p in owned} == {existing.id, created.id}
                assert (await db.execute(select(func.count()).select_from(CompetitorAnalysis))).scalar_one() == 1
                assert (await db.get(CompetitorAnalysis, row_id)).price == 99
        finally:
            await engine.dispose()
    asyncio.run(run())
