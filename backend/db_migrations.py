"""
Startup schema lifecycle (root fix for create_all drift).

Single source of schema truth = Alembic. On startup we bring the database to
`head` automatically so that: new model -> new migration -> app start -> migration
applied, with NO manual ALTER lists (the anti-pattern that let review_responses
drift and 500 in production-class runtime).

env.py runs migrations on its own async engine via asyncio.run(), so the Alembic
command API must be invoked from a worker thread (no running event loop there).
Call `run_migrations()` from the async startup; it offloads to a thread.
"""
from __future__ import annotations

import logging
import os

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from config import settings

log = logging.getLogger("db")

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _alembic_config() -> Config:
    cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
    # absolute script_location so it works regardless of CWD
    cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "alembic"))
    return cfg


def _sync_url() -> str:
    """A synchronous URL for cheap inspection (async drivers can't run sync inspect)."""
    url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")


def _inspect() -> tuple[str | None, list[str]]:
    """Return (current_revision, app_table_names) of the live DB."""
    engine = sa.create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            tables = [t for t in sa.inspect(conn).get_table_names() if t != "alembic_version"]
        return current, tables
    finally:
        engine.dispose()


def _migrate_sync() -> None:
    cfg = _alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        current, app_tables = _inspect()
    except Exception:
        # Inspection driver unavailable (e.g. prod postgres without a sync driver).
        # Safe fallback: a plain upgrade is idempotent for empty/stamped DBs.
        log.warning("[DB] schema inspection unavailable — running upgrade head directly")
        command.upgrade(cfg, "head")
        log.info("[DB] Migration complete -> %s", head)
        return

    log.info("[DB] Alembic current: %s", current or "(none)")
    log.info("[DB] Alembic head:    %s", head)

    if current == head:
        log.info("[DB] Schema up-to-date")
        return

    if current is None and app_tables:
        # Legacy DB created by the old create_all bootstrap (no alembic_version).
        # We cannot replay the baseline over existing tables; declare it at head.
        # One-time transition — log loudly so any residual column drift is visible.
        log.warning(
            "[DB] Legacy un-stamped database with %d tables detected — stamping head. "
            "If a column is missing, reconcile once via Alembic; future migrations "
            "apply automatically from here.", len(app_tables),
        )
        command.stamp(cfg, "head")
        log.info("[DB] Stamped head (%s)", head)
        return

    # Fresh DB (no tables) or a stamped DB behind head: apply migrations.
    log.info("[DB] Applying migrations...")
    command.upgrade(cfg, "head")
    log.info("[DB] Migration complete -> %s", head)


async def run_migrations() -> None:
    """Async entrypoint for startup. Offloads to a thread because Alembic's env.py
    calls asyncio.run() internally, which fails inside a running event loop."""
    import asyncio

    if not settings.auto_migrate:
        log.warning("[DB] auto_migrate disabled — skipping startup migrations "
                    "(expecting migrations applied at deploy time)")
        return
    try:
        await asyncio.to_thread(_migrate_sync)
    except Exception:
        # Surface loudly; do not silently continue with a drifted schema.
        log.exception("[DB] Startup migration FAILED — schema may be out of date")
        raise
