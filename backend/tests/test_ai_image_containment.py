"""Unreleased SEO image entry points are inert even with a synthetic provider key."""
import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from database import get_db
from dependencies import get_current_user
from routers import ai_image_service as images
import rate_limit


class RetainedTasks(dict):
    """Keep synthetic records, but prevent even baseline tests from mutating them."""
    def _no_mutation(self, *args, **kwargs):
        raise AssertionError("retained task store must not be mutated")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _no_mutation


@pytest.fixture(params=["", "SYNTHETIC-NEVER-SEND"], ids=["no-key", "synthetic-configured-key"])
def boundary(request, monkeypatch):
    touched = []
    key_reads = []

    class SyntheticSettings:
        @property
        def nano_banana_api_key(self):
            key_reads.append("provider-key")
            return request.param

    def forbidden(name):
        def fail(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"unreleased image service reached {name}")
        return fail

    store = RetainedTasks({
        "known-pending": {"status": "processing", "stage": "queued", "preset": "minimal",
                          "category": "auto", "product_name": "Synthetic retained product",
                          "image_urls": None, "slide_statuses": ["pending"] * 6,
                          "completed_count": 0, "created_at": "2026-11-01T00:00:00"},
        "known-done": {"status": "done", "stage": "done", "preset": "minimal",
                       "category": "auto", "product_name": "Synthetic retained product",
                       "image_urls": ["data:image/svg+xml;base64,U1lOVEhFVElD"] * 6,
                       "slide_statuses": ["done"] * 6, "completed_count": 6},
    })
    snapshot = deepcopy(dict(store))
    monkeypatch.setattr(images, "_tasks", store)  # original object is restored, never cleared
    monkeypatch.setattr(images, "settings", SyntheticSettings())
    monkeypatch.setattr(images, "_call_ai_api_parallel", AsyncMock(side_effect=forbidden("parallel-provider")))
    monkeypatch.setattr(images, "_call_slide", AsyncMock(side_effect=forbidden("slide-provider")))
    monkeypatch.setattr(images, "_build_prompt", Mock(side_effect=forbidden("prompt-builder")))
    monkeypatch.setattr(images.httpx, "AsyncClient", Mock(side_effect=forbidden("provider-client")))
    monkeypatch.setattr(rate_limit._limiter, "hit", AsyncMock(side_effect=forbidden("limiter-hit")))
    result = SimpleNamespace(touched=touched, key_reads=key_reads, store=store,
                             snapshot=snapshot, forbidden=forbidden)
    yield result
    assert dict(store) == snapshot


VALID = {"preset": "minimal", "category": "auto", "product_name": "Synthetic product only"}
CASES = []
for endpoint in ("generate-background", "generate-single", "suggest-text"):
    valid = {**VALID, "slide_idx": 0} if endpoint == "generate-single" else VALID
    for label, body in (("valid", valid), ("invalid-schema", {}), ("malformed-json", b"{")):
        CASES.append(pytest.param("POST", f"/api/ai/{endpoint}", body, id=f"{endpoint}-{label}"))
    if endpoint != "suggest-text":
        CASES.append(pytest.param("POST", f"/api/ai/{endpoint}", {**valid, "preset": "invalid"},
                                  id=f"{endpoint}-invalid-preset"))
for task_id in ("known-pending", "known-done", "unknown"):
    CASES.append(pytest.param("GET", f"/api/ai/task/{task_id}", None, id=f"task-{task_id}"))


@pytest.mark.parametrize("identity", ["anonymous", "active", "deleted"])
@pytest.mark.parametrize("method,path,body", CASES)
def test_asgi_returns_stable_404_before_validation_auth_limiter_or_provider(boundary, identity, method, path, body):
    app = FastAPI()
    app.include_router(images.router, prefix="/api")
    synthetic_user = SimpleNamespace(id=f"synthetic-{identity}",
                                     deleted_at=datetime(2026, 1, 1) if identity == "deleted" else None)

    async def forbidden_auth():
        boundary.forbidden(f"auth-{synthetic_user.id}")()

    async def forbidden_db():
        boundary.forbidden("database")()
        yield  # pragma: no cover

    async def forbidden_limiter():
        boundary.forbidden("limiter-dependency")()

    app.dependency_overrides[get_current_user] = forbidden_auth
    app.dependency_overrides[get_db] = forbidden_db
    app.dependency_overrides[images.limit_ai] = forbidden_limiter
    headers = {} if identity == "anonymous" else {"Cookie": f"pult_session_dev=synthetic-{identity}"}
    kwargs = {"content": body, "headers": {**headers, "Content-Type": "application/json"}} if isinstance(body, bytes) else {"json": body, "headers": headers}
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, path, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert boundary.touched == []
    assert boundary.key_reads == []
    assert dict(boundary.store) == boundary.snapshot


@pytest.mark.parametrize("name", ["generate_background", "generate_single", "suggest_text", "get_task_status"])
@pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
def test_direct_handlers_fail_closed_without_touching_tasks_or_provider(boundary, name, valid):
    if name == "get_task_status":
        kwargs = {"task_id": "known-done" if valid else "unknown"}
    else:
        model = {"generate_background": images.GenerateRequest,
                 "generate_single": images.GenerateSingleRequest,
                 "suggest_text": images.SuggestTextRequest}[name]
        kwargs = {"body": model(**{**VALID, "slide_idx": 0}) if valid else object()}
        if name != "suggest_text":
            kwargs["request"] = Request({"type": "http"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(images, name)(**kwargs))
    assert error.value.status_code == 404
    assert error.value.detail == "Not found"
    assert boundary.touched == []
    assert boundary.key_reads == []
    assert dict(boundary.store) == boundary.snapshot


def test_worker_returns_without_polling_dequeue_egress_or_changing_retained_records(boundary, monkeypatch):
    class UnreadTasks(RetainedTasks):
        def _no_read(self, *args, **kwargs):
            boundary.forbidden("queue-read")()
        items = values = keys = get = __getitem__ = __iter__ = _no_read

    protected = UnreadTasks(boundary.snapshot)
    monkeypatch.setattr(images, "_tasks", protected)
    ticks = []

    async def bounded_tick(*args, **kwargs):
        ticks.append("sleep")
        if len(ticks) > 1:
            raise AssertionError("worker must return rather than polling")

    monkeypatch.setattr(images.asyncio, "sleep", bounded_tick)
    previous_running = images._worker_running

    async def scheduled_as_in_main():
        task = asyncio.create_task(images.queue_worker())
        result = await task
        assert task.done()
        return result

    assert asyncio.run(scheduled_as_in_main()) is None
    assert ticks == []
    assert boundary.touched == []
    assert boundary.key_reads == []
    assert images._worker_running is previous_running
    assert dict.items(protected) == dict.items(boundary.snapshot)


def test_quarantine_is_scoped_to_exact_router_and_does_not_disable_other_routes(boundary):
    assert len(images.router.routes) == 4
    paths = {route.path for route in images.router.routes}
    assert paths == {"/ai/generate-background", "/ai/task/{task_id}", "/ai/generate-single", "/ai/suggest-text"}
    assert {route.path: route.methods for route in images.router.routes} == {
        "/ai/generate-background": {"POST"}, "/ai/task/{task_id}": {"GET"},
        "/ai/generate-single": {"POST"}, "/ai/suggest-text": {"POST"},
    }
    app = FastAPI()
    app.include_router(images.router, prefix="/api")

    @app.get("/api/containment-sentinel")
    async def sentinel():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/api/containment-sentinel")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert boundary.touched == []
    assert boundary.key_reads == []
