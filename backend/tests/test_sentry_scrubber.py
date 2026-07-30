"""
SECURITY-2A — Sentry PII/secret scrubber. Proves that before any event leaves the process, sensitive
data (Authorization/Cookie headers, passwords, tokens, api keys, Ozon Client-Id, nested secrets, query
tokens, stack-frame locals) is removed, while useful non-sensitive signal survives. Pure unit tests —
no DSN, no sentry-sdk init, no network.
"""
from services.sentry_setup import (
    _scrub, _scrub_event, _scrub_query_string, _is_sensitive_key, _REDACTED, init_sentry)


# ── key classification ───────────────────────────────────────────────────────
def test_sensitive_keys_detected():
    for k in ("Authorization", "cookie", "Set-Cookie", "access_token", "refresh_token",
              "password", "api_key", "x-api-key", "ozon_client_id", "client_secret",
              "yookassa_secret_key", "reset_token", "X-Internal-Key", "jwt", "csrf"):
        assert _is_sensitive_key(k), k
    for k in ("status", "route", "count", "user_id", "marketplace", "duration_ms"):
        assert not _is_sensitive_key(k), k


# ── recursive redaction, input not mutated ───────────────────────────────────
def test_scrub_recursive_and_pure():
    src = {
        "password": "hunter2",
        "nested": {"authorization": "Bearer abc", "safe": "keep",
                   "deep": [{"api_key": "AKIA123"}, {"count": 5}]},
        "list": ["ok", {"token": "t0ken"}],
        "status": 500,
    }
    out = _scrub(src)
    assert out["password"] == _REDACTED
    assert out["nested"]["authorization"] == _REDACTED
    assert out["nested"]["safe"] == "keep"
    assert out["nested"]["deep"][0]["api_key"] == _REDACTED
    assert out["nested"]["deep"][1]["count"] == 5
    assert out["list"][1]["token"] == _REDACTED
    assert out["status"] == 500
    # original object untouched
    assert src["password"] == "hunter2" and src["nested"]["authorization"] == "Bearer abc"


def test_scrub_depth_bounded_does_not_hang():
    d = cur = {}
    for _ in range(60):
        cur["next"] = {}
        cur = cur["next"]
    cur["password"] = "x"
    _scrub(d)   # must return without recursion error


# ── query string ─────────────────────────────────────────────────────────────
def test_query_string_token_redacted():
    out = _scrub_query_string("page=2&token=secretval&sort=name")
    assert "secretval" not in out and "page=2" in out and "sort=name" in out
    assert _REDACTED.strip("[]") in out or "redacted" in out


# ── full event ───────────────────────────────────────────────────────────────
def test_scrub_event_drops_body_and_headers():
    event = {
        "request": {
            "data": {"password": "hunter2", "token": "raw-marketplace-key"},   # request body
            "headers": {"Authorization": "Bearer xyz", "Cookie": "pult_token=abc", "User-Agent": "UA"},
            "cookies": {"pult_token": "abc"},
            "query_string": "a=1&reset_token=zzz",
            "url": "https://api/x",
        },
        "extra": {"api_key": "AKIA999", "safe_count": 3},
        "exception": {"values": [{"stacktrace": {"frames": [
            {"vars": {"password": "p", "n": 1}}]}}]},
        "transaction": "POST /api/connections",
        "level": "error",
    }
    out = _scrub_event(event)
    assert "data" not in out["request"]                                  # body dropped entirely
    assert out["request"]["headers"]["Authorization"] == _REDACTED
    assert out["request"]["headers"]["Cookie"] == _REDACTED
    assert out["request"]["headers"]["User-Agent"] == "UA"              # safe header kept
    assert out["request"]["cookies"] == _REDACTED
    assert "zzz" not in out["request"]["query_string"]
    assert out["extra"]["api_key"] == _REDACTED
    assert out["extra"]["safe_count"] == 3                              # safe extra kept
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["password"] == _REDACTED
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["n"] == 1
    assert out["transaction"] == "POST /api/connections"               # route signal preserved
    assert out["level"] == "error"


def test_scrub_event_survives_weird_shape():
    # a malformed event must not raise; worst case request/extra are dropped
    assert _scrub_event({"request": "not-a-dict", "extra": None, "level": "error"})["level"] == "error"
    assert _scrub_event("not-a-dict") == "not-a-dict"


# ── no DSN → init is a pure no-op (no network) ───────────────────────────────
def test_init_sentry_noop_without_dsn(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "sentry_dsn", "", raising=False)
    init_sentry()   # must return quietly, never import/init the SDK or make a network call
