"""Alembic async environment for PULT (Sprint 69 migration baseline).

Schema source of truth: SQLAlchemy models registered via `import models`.
DB URL resolution order:
  1. ALEMBIC_DATABASE_URL env var  (used for baseline generation / drift checks
     against a throwaway DB without touching the real one)
  2. config.settings.database_url  (normal dev / prod path)

Supports both sqlite+aiosqlite and postgresql+asyncpg drivers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from alembic import context

# ── Make the backend package importable (env.py lives in backend/alembic/) ──
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import importlib                     # noqa: E402
import pkgutil                        # noqa: E402

from config import settings          # noqa: E402
from database import Base            # noqa: E402
import models as _models_pkg         # noqa: E402


def _register_all_models() -> None:
    """Import every submodule of the `models` package so the FULL schema lands
    on Base.metadata.

    NOTE: models/__init__.py does not import every model (e.g. referral_record
    is registered at runtime only via a router import side-effect). Relying on
    that here would silently drop tables from the baseline. We import every
    models/*.py module explicitly so the migration registry is complete and
    self-contained, independent of import order elsewhere in the app.
    """
    for module in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"{_models_pkg.__name__}.{module.name}")


_register_all_models()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,   # batch mode keeps SQLite ALTERs portable
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
