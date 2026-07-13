"""Production CORS must not trust localhost. The allow-list included the four localhost dev
origins unconditionally, and with allow_credentials=True that let a page on a victim's own
localhost:3000 make credentialed calls to the prod API. In production the list is now only the
configured frontend origin; outside production the dev origins stay so local work is unchanged.
"""
import importlib

import pytest

from config import settings


def _build_allowed_origins():
    """Reproduce main.py's allow-list computation under the current settings, without importing
    the whole app (which spins up a scheduler). This mirrors the exact lines under test."""
    _DEV = [
        "http://localhost:3000", "http://localhost:3001",
        "http://127.0.0.1:3000", "http://127.0.0.1:3001",
    ]
    allowed = [] if settings.app_env == "production" else list(_DEV)
    if settings.frontend_url and settings.frontend_url not in allowed:
        allowed.append(settings.frontend_url)
    return allowed


def test_production_excludes_localhost(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "frontend_url", "https://app.biznes-pult.ru")
    allowed = _build_allowed_origins()

    assert allowed == ["https://app.biznes-pult.ru"]
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:3001", "http://127.0.0.1:3001"):
        assert origin not in allowed


def test_development_keeps_localhost(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
    allowed = _build_allowed_origins()

    assert "http://localhost:3000" in allowed
    assert "http://127.0.0.1:3000" in allowed


def test_main_module_matches_this_logic(monkeypatch):
    # Guard against the source drifting from the reproduced logic above: assert main.py's own
    # allow-list, recomputed under production, contains no localhost.
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "frontend_url", "https://prod.example.com")
    import main
    importlib.reload(main)
    try:
        assert all("localhost" not in o and "127.0.0.1" not in o for o in main._allowed_origins)
        assert "https://prod.example.com" in main._allowed_origins
    finally:
        monkeypatch.undo()
        importlib.reload(main)
