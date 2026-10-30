"""Unverified synthetic monitor data must remain unreachable and inert."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from database import get_db
from dependencies import get_current_user
from routers import monitor
from tasks import monitor_checker


CASES = [
    ("GET", "/monitor/events"),
    ("GET", "/monitor/events/synthetic-id"),
    ("GET", "/monitor/events/invalid-id"),
    ("POST", "/monitor/check"),
]


@pytest.mark.parametrize("method,path", CASES)
@pytest.mark.parametrize("cookie", [False, True])
def test_monitor_routes_refuse_before_auth_database_or_generator(method, path, cookie, monkeypatch):
    app = FastAPI()
    app.include_router(monitor.router, prefix="/api")
    touched = []

    async def forbidden_auth():
        touched.append("auth")
        raise AssertionError("unavailable monitor reached auth")

    async def forbidden_db():
        touched.append("db")
        raise AssertionError("unavailable monitor reached database")
        yield  # pragma: no cover

    generator = AsyncMock(side_effect=AssertionError("must not generate events"))
    monkeypatch.setattr(monitor_checker, "run_check", generator)
    app.dependency_overrides[get_current_user] = forbidden_auth
    app.dependency_overrides[get_db] = forbidden_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, f"/api{path}", headers={"Cookie": "synthetic=value"} if cookie else {})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert touched == []
    generator.assert_not_called()


@pytest.mark.parametrize("name", ["list_events", "get_event", "trigger_check"])
def test_direct_handlers_do_not_read_historical_rows_or_write(name):
    db = Mock()
    db.execute = AsyncMock(side_effect=AssertionError("must not read old events"))
    db.commit = AsyncMock(side_effect=AssertionError("must not commit"))
    kwargs = {"current_user": Mock(), "db": db}
    if name == "get_event":
        kwargs["event_id"] = "synthetic-id"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(getattr(monitor, name)(**kwargs))
    assert exc.value.status_code == 404
    db.execute.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_retired_checker_direct_call_is_inert():
    db = Mock()
    db.execute = AsyncMock(side_effect=AssertionError("must not read"))
    db.commit = AsyncMock(side_effect=AssertionError("must not write"))
    assert asyncio.run(monitor_checker.run_check(db)) == []
    db.execute.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_main_app_keeps_monitor_closed_without_starting_services(monkeypatch):
    from config import settings
    from main import app

    async def forbidden_auth():
        raise AssertionError("closed monitor reached auth")

    async def forbidden_db():
        raise AssertionError("closed monitor reached DB")
        yield  # pragma: no cover

    @asynccontextmanager
    async def forbidden_lifespan(_app):
        raise AssertionError("must not start services")
        yield  # pragma: no cover

    monkeypatch.setitem(app.dependency_overrides, get_current_user, forbidden_auth)
    monkeypatch.setitem(app.dependency_overrides, get_db, forbidden_db)
    monkeypatch.setattr(app.router, "lifespan_context", forbidden_lifespan)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        for method, path in CASES:
            response = client.request(method, f"/api{path}", headers={"Origin": settings.frontend_url})
            assert response.status_code == 404
            assert response.json() == {"detail": "Not found"}
    finally:
        client.close()
