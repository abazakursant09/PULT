"""
SECURITY-2A — the secret perimeter must fail-closed in EVERY non-development environment.

Before this change the SECRET_KEY / CRED_ENC_KEY hard-fail ran only when APP_ENV was the exact string
"production", so a server launched with APP_ENV=staging|beta|"prod"(typo)|unset-to-something-else and the
repo-public default SECRET_KEY would boot and sign forgeable JWTs. These tests prove the guard now covers
all non-dev environments and still stays permissive for development/test. Config validation runs at import
(module-level `sys.exit`), so each case imports `config` in a clean subprocess under a controlled env.

No real secrets are used; a strong key here is just `"a" * 64` (length only is checked).
"""
import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_STRONG_KEY = "a" * 64                       # >= 32 chars, not a known default → accepted
_CRED_KEY = "unit-test-fernet-key-placeholder-value"


def _import_config(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "SECRET_KEY": _STRONG_KEY, "CRED_ENC_KEY": _CRED_KEY, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )


# ── weak/default key is REFUSED in every non-development environment ──────────
def test_production_weak_key_fails():
    r = _import_config({"APP_ENV": "production", "SECRET_KEY": "dev-secret-key-change-in-production",
                        "DATABASE_URL": "postgresql+asyncpg://u:p@db/pult", "SMTP_HOST": "smtp.x.com",
                        "FRONTEND_URL": "https://app.pult.ru"})
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


def test_staging_weak_key_fails():
    r = _import_config({"APP_ENV": "staging", "SECRET_KEY": "dev-secret-key-change-in-production"})
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


def test_beta_weak_key_fails():
    r = _import_config({"APP_ENV": "beta", "SECRET_KEY": "dev-secret-key-change-in-production"})
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


def test_unknown_env_weak_key_fails():
    # a misspelled APP_ENV must NOT be treated as development
    r = _import_config({"APP_ENV": "prod", "SECRET_KEY": "dev-secret-key-change-in-production"})
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


def test_nondev_short_key_fails():
    r = _import_config({"APP_ENV": "staging", "SECRET_KEY": "zzqqweak"})   # 8 chars < 32, not a default
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


def test_nondev_missing_cred_enc_key_fails():
    r = _import_config({"APP_ENV": "staging", "CRED_ENC_KEY": ""})
    assert r.returncode != 0 and "CRED_ENC_KEY" in (r.stdout + r.stderr)


# ── strong key in a non-dev environment is ACCEPTED ──────────────────────────
def test_staging_strong_key_starts():
    r = _import_config({"APP_ENV": "staging"})     # strong key + cred key from base; op checks are prod-only
    assert r.returncode == 0, r.stdout + r.stderr


def test_env_is_case_insensitive_and_trimmed():
    r = _import_config({"APP_ENV": "  Staging  ", "SECRET_KEY": "dev-secret-key-change-in-production"})
    assert r.returncode != 0 and "SECRET_KEY" in (r.stdout + r.stderr)


# ── development / test stay permissive (weak default allowed) ─────────────────
def test_development_weak_key_starts():
    env = {**os.environ}
    for k in ("SECRET_KEY", "CRED_ENC_KEY", "SMTP_HOST", "DATABASE_URL", "FRONTEND_URL"):
        env.pop(k, None)
    env["APP_ENV"] = "development"
    r = subprocess.run([sys.executable, "-c", "import config"], cwd=BACKEND_DIR,
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_test_env_weak_key_starts():
    env = {**os.environ}
    for k in ("SECRET_KEY", "CRED_ENC_KEY", "SMTP_HOST"):
        env.pop(k, None)
    env["APP_ENV"] = "test"
    r = subprocess.run([sys.executable, "-c", "import config"], cwd=BACKEND_DIR,
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── the abort message never echoes the secret value ──────────────────────────
def test_error_message_does_not_leak_secret():
    r = _import_config({"APP_ENV": "staging", "SECRET_KEY": "zzqqweak"})
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "zzqqweak" not in out          # the weak value must never appear in logs/exit text
    assert "SECRET_KEY" in out            # but the operator is told which key to fix
