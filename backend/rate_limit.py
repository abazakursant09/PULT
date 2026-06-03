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

_NO_IP = "unknown"


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
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else None) or _NO_IP


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
