"""
SECURITY-2B-3 — API-response security headers (the HTML CSP is Next's job; this covers FastAPI).

Prod: locked-down JSON CSP (default-src 'none'), HSTS present. Dev: Swagger-friendly CSP, no HSTS.
Every /api/* response is no-store (never proxy-cache seller/financial JSON). COOP always on.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from main import SecurityHeadersMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/x")
    async def _api():
        return {"ok": True}

    @app.get("/health")
    async def _health():
        return {"ok": True}

    return TestClient(app)


def test_dev_headers(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    r = _client().get("/api/x")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp        # Swagger /docs needs inline
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert "strict-transport-security" not in r.headers      # no HSTS over local http
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cache-control"] == "no-store"


def test_prod_headers_locked_down(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    r = _client().get("/api/x")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("default-src 'none'")              # JSON API needs nothing
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "script-src" not in csp                            # no scripts served by the API in prod
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_all_api_responses_are_no_store(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    assert _client().get("/api/x").headers["cache-control"] == "no-store"


def test_non_api_not_forced_no_store(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    # a non-/api path (e.g. health) is not forced no-store by this middleware
    assert _client().get("/health").headers.get("cache-control") is None
