"""Architectural protection (Sprint 69 — Risk R1).

Constitutional invariant:

    SQLAlchemy model schema == Alembic head schema

This test builds a fresh database from the Alembic head and asserts that
`alembic check` reports no pending operations. It FAILS if the models and the
migration history have diverged — i.e. a model was changed without a matching
migration.

See docs/governance/schema_governance.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
CLEAN_MESSAGE = "No new upgrade operations detected."


def _run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ALEMBIC_DATABASE_URL"] = db_url
    env["APP_ENV"] = "development"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


def test_models_match_alembic_head() -> None:
    """Models and Alembic head must describe an identical schema."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "drift_check.db"
        db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

        up = _run_alembic("upgrade", "head", db_url=db_url)
        assert up.returncode == 0, f"alembic upgrade head failed:\n{up.stdout}\n{up.stderr}"

        check = _run_alembic("check", db_url=db_url)
        combined = check.stdout + check.stderr
        assert check.returncode == 0 and CLEAN_MESSAGE in combined, (
            "SCHEMA DRIFT DETECTED — models diverge from Alembic head. "
            "A model was changed without a migration. Run "
            "`python -m alembic revision --autogenerate -m <msg>` and review it.\n"
            f"--- alembic check output ---\n{combined}"
        )
