"""
SECURITY-2B-2 — single source of truth for the browser auth session cookie.

The session JWT lives ONLY in this cookie; it is HttpOnly (JavaScript can never read it), set and
cleared only by the backend, and never returned in a JSON body. One helper module so login / logout /
the auth dependency / tests can never drift on the cookie name or its attributes.

Environment split (host-only, never a Domain):
  * non-development (production / staging / beta / unknown APP_ENV): name `__Host-pult_session`,
    Secure=True. The `__Host-` prefix is a browser-enforced guarantee — it REQUIRES Secure + Path=/ +
    no Domain, so a cookie with that name cannot have been set for a parent domain or over plain HTTP.
  * development / test / local: name `pult_session_dev`, Secure=False (so it works over http://localhost;
    the `__Host-` prefix cannot be used without Secure). HttpOnly stays True even in dev.

SameSite=Lax + Path=/ everywhere. No Domain attribute (host-only) in any environment. This is a
same-site (single public origin, e.g. https://app.<domain> reverse-proxying /api → FastAPI) design; it
deliberately does NOT support cross-site auth / SameSite=None.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request, Response

from config import settings

# Weak/default handling already lives in config; reuse the same dev-env vocabulary for consistency.
_DEV_ENVS = {"development", "test", "local"}

_NAME_PROD = "__Host-pult_session"
_NAME_DEV = "pult_session_dev"
_SAMESITE = "lax"
_PATH = "/"


def _is_dev() -> bool:
    return (settings.app_env or "").strip().lower() in _DEV_ENVS


def cookie_name() -> str:
    """The session cookie name for the current environment (dev name has no `__Host-` prefix so it can
    be Secure=False over http://localhost)."""
    return _NAME_DEV if _is_dev() else _NAME_PROD


def is_secure() -> bool:
    """Secure flag: True everywhere except explicit development/test/local."""
    return not _is_dev()


def _max_age_seconds() -> int:
    return int(settings.access_token_expire_minutes) * 60


def set_session_cookie(response: Response, token: str) -> None:
    """Write the session JWT into the HttpOnly cookie. Never logs the token. No Domain (host-only)."""
    response.set_cookie(
        key=cookie_name(),
        value=token,
        max_age=_max_age_seconds(),
        httponly=True,
        secure=is_secure(),
        samesite=_SAMESITE,
        path=_PATH,
    )
    response.headers["Cache-Control"] = "no-store"   # never cache a response that carries Set-Cookie


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie with the SAME name/path/samesite/secure so the browser actually drops
    it. Idempotent — deleting an absent cookie is a no-op for the client."""
    response.delete_cookie(
        key=cookie_name(),
        path=_PATH,
        samesite=_SAMESITE,
        secure=is_secure(),
        httponly=True,
    )


def read_session_cookie(request: Request) -> Optional[str]:
    """Read the raw session JWT from the environment-appropriate cookie, or None."""
    return request.cookies.get(cookie_name())
