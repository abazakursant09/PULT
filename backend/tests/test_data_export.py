import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from dependencies import get_current_user
from routers import data_export


@pytest.fixture
def export_client():
    loop = asyncio.new_event_loop()
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()

    db = loop.run_until_complete(setup())
    uid = str(uuid.uuid4())
    app = FastAPI()
    app.include_router(data_export.router, prefix="/api")

    async def get_test_db():
        yield db

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    with TestClient(app) as client:
        yield client, db, uid, loop, app
    loop.run_until_complete(db.close())
    loop.run_until_complete(engine.dispose())
    loop.close()


def seed(db, loop, uid, **extra):
    for name, model, fields in data_export.DATASETS:
        values = dict(user_id=uid, import_id=str(uuid.uuid4()), marketplace="ozon", sku="Товар-1")
        values.update(extra)
        db.add(model(**values))
    loop.run_until_complete(db.commit())


def test_empty_export_and_headers(export_client):
    client, *_ = export_client
    response = client.get("/api/data/export/imports")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="pult-imported-data.json"'
    assert response.json()["scope"] == "imported_rows"
    assert all(value == [] for value in response.json()["datasets"].values())


def test_owner_only_allowlist_and_query_cannot_choose_user(export_client):
    client, db, uid, loop, _ = export_client
    seed(db, loop, uid)
    seed(db, loop, "other-owner", sku="OTHER-SECRET")
    response = client.get("/api/data/export/imports?user_id=other-owner")
    assert response.status_code == 200
    assert "OTHER-SECRET" not in response.text
    for name, _, fields in data_export.DATASETS:
        rows = response.json()["datasets"][name]
        assert len(rows) == 1
        assert set(rows[0]) == set(fields)
        assert rows[0]["sku"] == "Товар-1"
    for secret in ("hashed_password", "secret_enc", "temp_path", "registered_ip"):
        assert secret not in response.text


def test_row_overflow_rejects_not_truncates(export_client, monkeypatch):
    client, db, uid, loop, _ = export_client
    seed(db, loop, uid)
    monkeypatch.setattr(data_export, "MAX_ROWS", 3)
    response = client.get("/api/data/export/imports")
    assert response.status_code == 413
    assert "datasets" not in response.text


def test_size_overflow_rejects(export_client, monkeypatch):
    client, db, uid, loop, _ = export_client
    seed(db, loop, uid)
    monkeypatch.setattr(data_export, "MAX_BYTES", 100)
    assert client.get("/api/data/export/imports").status_code == 413


def test_rate_limit_verified_identity(export_client):
    client, *_ = export_client
    assert client.get("/api/data/export/imports").status_code == 200
    assert client.get("/api/data/export/imports").status_code == 200
    assert client.get("/api/data/export/imports").status_code == 429


def test_authentication_required(export_client):
    client, _, _, _, app = export_client
    del app.dependency_overrides[get_current_user]
    response = client.get("/api/data/export/imports")
    assert response.status_code in (401, 403)


def seed_analysis(db, loop, uid, text):
    for name, model, fields in data_export.ANALYSIS_DATASETS:
        values = dict(user_id=uid, created_at=datetime(2026, 11, 9, 12, 0))
        if name == "decisions":
            values.update(problem=text, action="Проверить отчёт", pnl_impact=12.5, status="dismissed")
        else:
            values.update(signal_key=name + "_test", what=text, sku="SKU", status="resolved")
            if hasattr(model, "audit_id"):
                values["audit_id"] = str(uuid.uuid4())
            values["requirement_type" if name == "legal" else "problem_type"] = "test"
        db.add(model(**values))
    loop.run_until_complete(db.commit())


def test_saved_analysis_all_explicit_sources_isolated_and_not_recomputed(export_client):
    client, db, uid, loop, _ = export_client
    seed_analysis(db, loop, uid, "Мои сохранённые результаты")
    seed_analysis(db, loop, "other-owner", "OTHER-ANALYSIS-SECRET")
    response = client.get("/api/data/export/analysis?user_id=other-owner")
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "saved_analysis"
    assert 'filename="pult-saved-analysis.json"' in response.headers["content-disposition"]
    assert "OTHER-ANALYSIS-SECRET" not in response.text
    for name, _, fields in data_export.ANALYSIS_DATASETS:
        rows = response.json()["datasets"][name]
        assert len(rows) == 1
        assert set(rows[0]) == set(fields)
        assert rows[0]["created_at"] == "2026-11-09T12:00:00"
        assert rows[0]["status"] == ("dismissed" if name == "decisions" else "resolved")
        assert rows[0]["problem" if name == "decisions" else "what"] == "Мои сохранённые результаты"


def test_analysis_limits_and_independent_fixed_rate_buckets(export_client, monkeypatch):
    client, db, uid, loop, _ = export_client
    seed_analysis(db, loop, uid, "example")
    monkeypatch.setattr(data_export, "MAX_ROWS", 1)
    assert client.get("/api/data/export/analysis").status_code == 413
    assert client.get("/api/data/export/imports").status_code == 200
    assert client.get("/api/data/export/imports").status_code == 200
    assert client.get("/api/data/export/analysis").status_code == 413
    assert client.get("/api/data/export/analysis").status_code == 429
    assert client.get("/api/data/export/imports?scope=arbitrary").status_code == 429


def test_analysis_authentication_required(export_client):
    client, _, _, _, app = export_client
    del app.dependency_overrides[get_current_user]
    assert client.get("/api/data/export/analysis").status_code in (401, 403)


def test_download_replay_does_not_starve_other_archive(export_client):
    client, *_ = export_client
    for archive in ("imports", "analysis"):
        assert client.get(f"/api/data/export/{archive}").status_code == 200
        assert client.get(f"/api/data/export/{archive}").status_code == 200
    for archive in ("imports", "analysis"):
        assert client.get(f"/api/data/export/{archive}").status_code == 429
