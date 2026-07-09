"""
P7.3 — Production config fail-fast.

In production the app must refuse to start unless the four CRITICAL secrets are
present and valid: SECRET_KEY (secure), DATABASE_URL (PostgreSQL), CRED_ENC_KEY,
SMTP_HOST. Verified by importing `config` in a subprocess (module-level validation
calls sys.exit) under controlled environments.
"""
import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_VALID_PROD = {
    "APP_ENV": "production",
    "SECRET_KEY": "a" * 64,                                    # secure, not a default
    "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/pult",
    "CRED_ENC_KEY": "unit-test-fernet-key-placeholder-value",
    "SMTP_HOST": "smtp.example.com",
}


def _import_config(overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **_VALID_PROD, **overrides}
    return subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )


# ── 1. complete production config → startup succeeds ─────────────────────────

def test_production_config_complete_starts():
    r = _import_config({})
    assert r.returncode == 0, r.stderr


# ── 2. each missing critical secret → startup fails fast ─────────────────────

def test_missing_secret_key_fails():
    r = _import_config({"SECRET_KEY": "dev-secret-key-change-in-production"})
    assert r.returncode != 0
    assert "SECRET_KEY" in (r.stdout + r.stderr)


def test_sqlite_database_fails():
    r = _import_config({"DATABASE_URL": "sqlite+aiosqlite:///./x.db"})
    assert r.returncode != 0
    assert "DATABASE_URL" in (r.stdout + r.stderr)


def test_missing_cred_enc_key_fails():
    r = _import_config({"CRED_ENC_KEY": ""})
    assert r.returncode != 0
    assert "CRED_ENC_KEY" in (r.stdout + r.stderr)


def test_missing_smtp_host_fails():
    r = _import_config({"SMTP_HOST": ""})
    assert r.returncode != 0
    assert "SMTP_HOST" in (r.stdout + r.stderr)


# ── 3. development stays permissive (defaults, no hard-fail) ──────────────────

def test_development_config_permissive():
    env = {**os.environ}
    for k in _VALID_PROD:
        env.pop(k, None)
    env["APP_ENV"] = "development"
    env["CRED_ENC_KEY"] = ""
    env["SMTP_HOST"] = ""
    r = subprocess.run([sys.executable, "-c", "import config"],
                       cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
