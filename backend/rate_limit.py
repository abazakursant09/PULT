"""
Sliding-window in-memory rate limiter.
ASGI-safe: asyncio Lock ensures no concurrent mutation.
Memory: buckets are bounded by window_s; old entries are pruned on every hit.
"""
import base64
import json
import time
import asyncio
from collections import defaultdict
from fastapi import Request, HTTPException

from config import settings

_NO_IP = "unknown"


def client_ip(request: Request) -> str:
    """The client IP used for every rate-limit identity.

    X-Forwarded-For is a chain: `client, proxy1, ..., proxyN`, where each proxy APPENDS
    the address it saw. The leftmost entries are whatever the client sent — fully
    spoofable — so reading `[0]` (as the old code did) let an attacker rotate the header
    and get a fresh rate-limit bucket per request, defeating the login/import limiters and
    the registration IP cap.

    We instead trust only the entries OUR OWN infrastructure appended, counting from the
    right. With `trusted_proxy_count = N`, the real client is the Nth value from the end;
    everything to its left is untrusted and ignored. With N = 0 (the default) there is no
    trusted proxy, so X-Forwarded-For is ignored entirely and the direct peer is used —
    fail-safe: a forged header buys nothing until an operator states how many proxies are
    real (behind one Caddy/nginx, TRUSTED_PROXY_COUNT=1).
    """
    direct = (request.client.host if request.client else None) or _NO_IP
    n = settings.trusted_proxy_count
    if n <= 0:
        return direct
    fwd = request.headers.get("X-Forwarded-For")
    if not fwd:
        return direct
    parts = [p.strip() for p in fwd.split(",") if p.strip()]
    # Header shorter than the declared proxy depth → it did not pass through the expected
    # chain, so it cannot be trusted. Fall back to the direct peer.
    if len(parts) < n:
        return direct
    return parts[-n]


class _SlidingWindow:
    def __init__(self) -> None:
        self._ts: defaultdict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_s: int) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_s
            bucket = [t for t in self._ts[key] if t > cutoff]
            if len(bucket) >= limit:
                raise HTTPException(status_code=429, detail="Too many requests")
            bucket.append(now)
            self._ts[key] = bucket


_limiter = _SlidingWindow()


def _ip(request: Request) -> str:
    return client_ip(request)


def _uid(request: Request) -> str:
    """Best-effort user-id from JWT sub — no signature verification."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload_b64 = auth.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.b64decode(payload_b64))
            uid = payload.get("sub")
            if uid:
                return str(uid)
        except Exception:
            pass
    return _ip(request)


async def limit_auth(request: Request) -> None:
    """5 req / 60 s per IP — login, forgot-password."""
    await _limiter.hit(f"auth:{_ip(request)}", limit=5, window_s=60)


async def limit_ai(request: Request) -> None:
    """10 req / 60 s per user — AI generation."""
    await _limiter.hit(f"ai:{_uid(request)}", limit=10, window_s=60)


async def limit_rebuild(request: Request) -> None:
    """3 req / 60 s per user — heavy rebuild track."""
    await _limiter.hit(f"rebuild:{_uid(request)}", limit=3, window_s=60)


async def limit_import(request: Request) -> None:
    """5 req / 600 s per user — CSV import."""
    await _limiter.hit(f"import:{_uid(request)}", limit=5, window_s=600)


async def limit_mfa(subject: str, request: Request) -> None:
    """5 attempts / 300 s per MFA subject (+ client IP) — TOTP verification.

    /login/mfa had no limiter, so an attacker holding a valid password (already past
    /login) could spray unlimited 6-digit codes at a 5-min mfa_token and brute-force the
    second factor. The key is the MFA subject, not just the IP, so the throttle follows
    the account being attacked even across IPs; the IP is appended so one victim account
    cannot be locked out globally by an attacker from a single address. 5 tries per 5 min
    leaves TOTP's 10^6 space effectively unbrute-forceable while a real user's fat-finger
    retries still go through.
    """
    await _limiter.hit(f"mfa:{subject}:{client_ip(request)}", limit=5, window_s=300)
