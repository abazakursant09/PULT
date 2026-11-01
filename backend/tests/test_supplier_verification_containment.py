"""Pseudo verification is unavailable; historical supplier data is retained."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models.supplier import Supplier
from models.supplier_verification import SupplierVerification
from models.user import User
from routers import supplier_verification as verification, suppliers_catalog as catalog
from services import verification as verification_service


CASES = [
    ("POST", "/suppliers/verify"),
    ("GET", "/suppliers/verify/my"),
    ("GET", "/suppliers/verify/synthetic-id"),
    ("DELETE", "/suppliers/verify/synthetic-id"),
    ("GET", "/suppliers/verified"),
]
BODY = {"company_name": "Synthetic manufacturer", "country": "russia",
        "inn": "1111111111", "legal_address": "Synthetic audit address only"}


@pytest.mark.parametrize("method,path", CASES)
@pytest.mark.parametrize("cookie", [False, True])
def test_verification_refuses_before_auth_db_or_stub(method, path, cookie, monkeypatch):
    app = FastAPI()
    app.include_router(verification.router, prefix="/api")
    touched = []

    async def forbidden_auth():
        touched.append("auth")
        raise AssertionError("closed verification reached authentication")

    async def forbidden_db():
        touched.append("db")
        raise AssertionError("closed verification reached DB")
        yield  # pragma: no cover

    stub = Mock(side_effect=AssertionError("pseudo verifier must never run"))
    monkeypatch.setattr(verification_service, "run_verification", stub)
    if hasattr(verification, "run_verification"):
        monkeypatch.setattr(verification, "run_verification", stub)
    app.dependency_overrides[get_current_user] = forbidden_auth
    app.dependency_overrides[get_db] = forbidden_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, f"/api{path}", json=BODY if method == "POST" else None,
                                  headers={"Cookie": "pult_session_dev=synthetic"} if cookie else {})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert touched == []
    stub.assert_not_called()


@pytest.mark.parametrize("name", ["submit_verification", "my_verifications", "get_verification",
                                 "revoke_verification", "list_verified"])
def test_direct_handlers_are_inert(name):
    db = Mock()
    db.execute = AsyncMock(side_effect=AssertionError("must not read records"))
    db.commit = AsyncMock(side_effect=AssertionError("must not write records"))
    kwargs = {"db": db}
    if name != "list_verified":
        kwargs["current_user"] = Mock(id="synthetic-owner")
    if name == "submit_verification":
        kwargs["data"] = verification.SupplierVerificationCreate(**BODY)
    if name in ("get_verification", "revoke_verification"):
        kwargs["verification_id"] = "synthetic-id"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(getattr(verification, name)(**kwargs))
    assert exc.value.status_code == 404
    db.execute.assert_not_called()
    db.add.assert_not_called()
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("value", ["true", "false", "1", "0"])
def test_catalog_verification_filter_refuses_before_db(value):
    app = FastAPI()
    app.include_router(catalog.router, prefix="/api")
    touched = []

    async def forbidden_db():
        touched.append("db")
        raise AssertionError("unsupported verification filter reached DB")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = forbidden_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/catalog/suppliers?verified={value}")
    assert response.status_code == 404
    assert touched == []


@pytest.mark.parametrize("value", [True, False])
def test_catalog_direct_filter_guard_does_not_read(value):
    db = Mock()
    db.execute = AsyncMock(side_effect=AssertionError("must not read verification flags"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(catalog.list_suppliers(industry=None, region=None, country=None,
                                           verified=value, search=None, sort="name", db=db))
    assert exc.value.status_code == 404
    db.execute.assert_not_called()


def test_catalog_stays_readable_without_endorsing_or_mutating_legacy_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic-suppliers.db'}")
    tables = [User.__table__, Supplier.__table__, SupplierVerification.__table__]
    User.metadata.create_all(engine, tables=tables)
    try:
        with Session(engine) as db:
            db.add(User(id="synthetic-owner", email="supplier-audit@example.invalid",
                        name="Synthetic", hashed_password="not-a-login-hash"))
            db.add(Supplier(id="supplier-1", company_name="Synthetic catalogue company", country="russia",
                            industry="synthetic", website="https://example.invalid", is_verified=True))
            for status in ("pending", "verified", "rejected"):
                db.add(SupplierVerification(id=f"sv-{status}", user_id="synthetic-owner",
                                            company_name="Synthetic historical application", country="russia",
                                            status=status, verification_source="manual",
                                            verified_at=datetime(2026, 1, 1) if status == "verified" else None))
            db.commit()

        def snapshot():
            with engine.connect() as connection:
                return {table.name: [dict(row._mapping) for row in connection.execute(select(table).order_by(table.c.id))]
                        for table in tables}

        before = snapshot()
        with Session(engine) as db:
            class ReadSession:
                async def execute(self, query):
                    return db.execute(query)

                async def get(self, model, row_id):
                    return db.get(model, row_id)

            app = FastAPI()
            app.include_router(verification.router, prefix="/api")
            app.include_router(catalog.router, prefix="/api")

            async def read_db():
                yield ReadSession()

            async def owner():
                return Mock(id="synthetic-owner")

            app.dependency_overrides[get_db] = read_db
            app.dependency_overrides[get_current_user] = owner
            with TestClient(app, raise_server_exceptions=False) as client:
                for method, path in CASES:
                    response = client.request(method, f"/api{path}", json=BODY if method == "POST" else None)
                    assert response.status_code == 404
                for path in ("/api/catalog/suppliers", "/api/catalog/suppliers/supplier-1",
                             "/api/catalog/suppliers?industry=synthetic&country=russia&sort=name&search=Synthetic"):
                    response = client.get(path)
                    assert response.status_code == 200
                    result = response.json()
                    rows = result if isinstance(result, list) else [result]
                    assert len(rows) == 1
                    assert rows[0]["id"] == "supplier-1"
                    assert rows[0]["is_verified"] is False
                    assert rows[0]["website"] == "https://example.invalid"
        assert snapshot() == before
    finally:
        engine.dispose()


def test_main_mount_does_not_bypass_verification_quarantine(monkeypatch):
    from config import settings
    from main import app

    async def forbidden_db():
        raise AssertionError("main app reached DB")
        yield  # pragma: no cover

    async def forbidden_auth():
        raise AssertionError("main app reached auth")

    @asynccontextmanager
    async def forbidden_lifespan(_app):
        raise AssertionError("must not start services")
        yield  # pragma: no cover

    monkeypatch.setitem(app.dependency_overrides, get_db, forbidden_db)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, forbidden_auth)
    monkeypatch.setattr(app.router, "lifespan_context", forbidden_lifespan)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        for method, path in CASES:
            response = client.request(method, f"/api{path}", json=BODY if method == "POST" else None,
                                      headers={"Origin": settings.frontend_url})
            assert response.status_code == 404
            assert response.json() == {"detail": "Not found"}
    finally:
        client.close()
