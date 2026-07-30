"""
Sentry backend setup + PII/secret scrubber (SECURITY-2A). Call init_sentry() in main.py before app
creation.

Graceful by design: no SENTRY_DSN → no-op (no network, no init); sentry-sdk missing → warn and
continue. The application always runs whether or not error tracking is enabled.

Every outbound event passes through `_scrub_event`, which runs BEFORE the event leaves the process:
request bodies are dropped entirely, and any header/cookie/query/extra/stack-local whose key looks
sensitive (token, secret, api_key, authorization, cookie, password, client-id/secret, credential, jwt,
otp, dsn, x-internal-key, …) is replaced with a redaction marker — recursively, at any depth. What
stays: the useful, non-sensitive signal (error type, transaction/route, status, numeric context).

Nothing here ever prints or forwards a secret value; the scrubber is pure (returns copies, never mutates
the caller's objects) so it is unit-testable without a DSN and without any network call.
"""
import logging
from typing import Any

from config import settings

log = logging.getLogger(__name__)

_REDACTED = "[redacted]"

# A key is sensitive if any of these substrings appears in its lowercased name. Substring match so
# `x-api-key`, `refresh_token`, `ozon_client_secret`, `set-cookie`, `yookassa_secret_key` are all caught.
_SENSITIVE_KEY_PARTS = (
    "authorization", "cookie", "token", "secret", "password", "passwd", "pwd",
    "api_key", "apikey", "api-key", "client-id", "client_id", "client-secret", "client_secret",
    "credential", "jwt", "otp", "dsn", "private", "x-internal-key", "internal-key", "internal_key",
    "session", "csrf", "signature",
)


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    return any(part in k for part in _SENSITIVE_KEY_PARTS)


def _scrub(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive-looking keys in dicts/lists. Pure: returns a new structure and never
    mutates the input. Depth-bounded so a pathological/cyclic structure cannot loop forever."""
    if _depth > 24:
        return _REDACTED
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _REDACTED if _is_sensitive_key(k) else _scrub(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        scrubbed = [_scrub(v, _depth + 1) for v in obj]
        return type(obj)(scrubbed) if isinstance(obj, tuple) else scrubbed
    return obj


def _scrub_query_string(qs: Any) -> Any:
    """Redact token-like params from a raw `a=1&token=xyz` query string, keeping structure/other params."""
    if not isinstance(qs, str) or "=" not in qs:
        return qs
    from urllib.parse import parse_qsl, urlencode
    pairs = [(k, _REDACTED if _is_sensitive_key(k) else v) for k, v in parse_qsl(qs, keep_blank_values=True)]
    return urlencode(pairs)


def _scrub_event(event: Any, hint: Any = None) -> Any:
    """Sentry before_send hook. Drops the request body outright and scrubs headers/cookies/query/extra/
    contexts/stack-local vars. Never raises — a scrub failure must not break error reporting, so on any
    unexpected shape it drops the whole request/extra rather than risk leaking it."""
    if not isinstance(event, dict):
        return event
    try:
        req = event.get("request")
        if isinstance(req, dict):
            req.pop("data", None)          # never forward the request body (passwords / marketplace keys live here)
            if isinstance(req.get("headers"), dict):
                req["headers"] = _scrub(req["headers"])
            if isinstance(req.get("cookies"), (dict, list)):
                req["cookies"] = _REDACTED
            if "query_string" in req:
                req["query_string"] = _scrub_query_string(req.get("query_string"))
        for k in ("extra", "contexts", "tags"):
            if isinstance(event.get(k), (dict, list)):
                event[k] = _scrub(event[k])
        # stack-frame local variables can hold a decoded token / password / credential object
        for value in (event.get("exception") or {}).get("values", []) or []:
            frames = (value.get("stacktrace") or {}).get("frames", []) or []
            for frame in frames:
                if isinstance(frame, dict) and isinstance(frame.get("vars"), dict):
                    frame["vars"] = _scrub(frame["vars"])
    except Exception:                       # defensive: never let scrubbing throw out of before_send
        event.pop("request", None)
        event.pop("extra", None)
    return event


def init_sentry() -> None:
    dsn = settings.sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app_env,
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            ignore_errors=[KeyboardInterrupt],
            send_default_pii=False,          # never attach cookies / auth headers / client IP by default
            max_request_body_size="never",   # never capture request bodies (passwords / marketplace keys)
            before_send=_scrub_event,        # belt-and-braces: redact anything sensitive that slipped in
            before_send_transaction=_scrub_event,
        )
        log.info("Sentry initialized (env=%s)", settings.app_env)
    except ImportError:
        log.warning("sentry-sdk not installed; error tracking disabled")
