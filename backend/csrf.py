"""
SECURITY-2B-2 — central Origin/Referer CSRF guard for cookie-authenticated browser mutations.

The session now rides in a cookie the browser attaches automatically, so a state-changing request
(POST/PUT/PATCH/DELETE) to /api/* must prove it came from our own frontend origin. SameSite=Lax already
blocks the classic cross-site form POST; this is the server-side defence-in-depth.

Model:
  * Only POST/PUT/PATCH/DELETE under /api/* are checked. Safe methods (GET/HEAD/OPTIONS) pass — the
    CORS preflight OPTIONS is never blocked here.
  * The request must carry an `Origin` that exactly matches the configured frontend origin (normalized
    scheme+host+port; NO suffix/substring matching). `Origin: null` is rejected.
  * If `Origin` is absent, fall back to a strict `Referer` allowlist (same normalized comparison). No
    Origin and no allowed Referer → 403.
  * X-Forwarded-Host is NOT trusted — only the literal Origin/Referer the browser sent.

Explicit, tested exemptions (server-to-server, each with its OWN server-side auth — never cookie-auth):
  * POST /api/payments/webhook          — YooKassa webhook; re-verifies status server-to-server.
  * POST /api/decisions/measurements/close-due — cron; X-Internal-Key HMAC.
  * /api/admin/promo…                    — operator; X-Internal-Key HMAC (all routes under this prefix
                                           are internal-key gated; there is no cookie-auth route here).
No broad path-prefix exemption is used for any tree that contains a cookie-auth route.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from config import settings

_STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEV_ENVS = {"development", "test", "local"}

_EXEMPT_EXACT = {
    "/api/payments/webhook",
    "/api/decisions/measurements/close-due",
}
_EXEMPT_PREFIX = ("/api/admin/promo",)

# SECURITY-2D-1C-C3B — NARROW exemption for the three machine-to-machine operator resolution POSTs. NOT a
# broad `/api/internal/recovery` prefix bypass: only POST to exactly
# /api/internal/recovery/operations/<log_id>/{confirm-applied|confirm-not-applied|close} is exempt. Every
# such route is X-Internal-Key gated (routers.internal_recovery._require_operator) — no cookie-auth route
# is ever released. Any other method, extra suffix, unknown action, or lookalike path stays protected.
_RECOVERY_RESOLUTION_ACTIONS = frozenset({"confirm-applied", "confirm-not-applied", "close"})


def _is_recovery_resolution_post(method: str, path: str) -> bool:
    if method != "POST":
        return False
    parts = path.split("/")
    # ['', 'api', 'internal', 'recovery', 'operations', '<log_id>', '<action>']
    return (len(parts) == 7
            and parts[:5] == ["", "api", "internal", "recovery", "operations"]
            and parts[5] != ""
            and parts[6] in _RECOVERY_RESOLUTION_ACTIONS)


def _dev() -> bool:
    return (settings.app_env or "").strip().lower() in _DEV_ENVS


def _normalize(url: Optional[str]) -> Optional[str]:
    """scheme://host:port with the default port made explicit, or None if unparseable. Path/query are
    dropped so a Referer like https://app/login compares equal to the https://app origin."""
    if not url:
        return None
    try:
        p = urlparse(url)
        if not p.scheme or not p.hostname:
            return None
        # p.port raises ValueError on a malformed authority (e.g. host:3000.evil.example) — that is a
        # reject, not a crash: an unparseable origin can never match the allowlist.
        port = p.port or (443 if p.scheme == "https" else 80 if p.scheme == "http" else 0)
    except ValueError:
        return None
    return f"{p.scheme}://{p.hostname}:{port}"


def _allowed() -> set[str]:
    raw = {settings.frontend_url}
    if _dev():
        raw |= {"http://localhost:3000", "http://localhost:3001",
                "http://127.0.0.1:3000", "http://127.0.0.1:3001"}
    out = {_normalize(o) for o in raw if o}
    out.discard(None)
    return out


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or any(path.startswith(pfx) for pfx in _EXEMPT_PREFIX)


def _reject(reason: str) -> JSONResponse:
    # Message is generic — no internal detail (allowed origins, config) leaks to the caller.
    return JSONResponse(status_code=403, content={"detail": "Запрос отклонён: источник не подтверждён"})


class OriginCsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if (request.method in _STATE_METHODS and path.startswith("/api/")
                and not _is_exempt(path)
                and not _is_recovery_resolution_post(request.method, path)):
            allowed = _allowed()
            origin = request.headers.get("origin")
            if origin is not None:
                if origin == "null" or _normalize(origin) not in allowed:
                    return _reject("origin")
            else:
                # No Origin header (rare for state-changing fetch) → strict Referer allowlist.
                if _normalize(request.headers.get("referer")) not in allowed:
                    return _reject("referer")
        return await call_next(request)
