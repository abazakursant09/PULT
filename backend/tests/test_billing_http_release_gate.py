"""The disabled commercial API must stop before authentication, DB or provider calls."""
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_db
from dependencies import get_current_user
from routers import payments


@pytest.mark.parametrize("flag", [False, None, 1, "true"])
@pytest.mark.parametrize("method,path", [
    ("POST", "/payments/create"),
    ("GET", "/payments/status/synthetic-id"),
    ("GET", "/payments/history"),
    ("POST", "/payments/webhook"),
])
def test_non_true_billing_flag_closes_mounted_http_surface_without_side_effects(flag, method, path, monkeypatch):
    monkeypatch.setattr(payments.settings, "billing_enabled", flag)
    app = FastAPI()
    app.include_router(payments.router, prefix="/api")
    touched = []

    async def forbidden_auth():
        touched.append("auth")
        raise AssertionError("disabled billing reached authentication")

    async def forbidden_db():
        touched.append("db")
        raise AssertionError("disabled billing reached database")
        yield  # pragma: no cover

    app.dependency_overrides[get_current_user] = forbidden_auth
    app.dependency_overrides[get_db] = forbidden_db
    provider = Mock(side_effect=AssertionError("disabled billing reached provider"))
    monkeypatch.setattr(payments.httpx, "AsyncClient", provider)
    template = "/api/payments/status/{payment_id}" if "/status/" in path else "/api" + path
    assert method.lower() in app.openapi()["paths"][template]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, "/api" + path, json={"tariff": "basic"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert touched == []
    provider.assert_not_called()
