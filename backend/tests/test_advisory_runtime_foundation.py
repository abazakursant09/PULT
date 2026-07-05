"""
Advisory Runtime — Phase-0 FOUNDATION guard.

Locks the foundation contracts + the advisory_run ledger shape, and proves the
foundation runs nothing and touches no existing runtime:
  * migration roundtrip (advisory_run created / dropped), single head;
  * AdvisoryRun has NO typed counters (stats is opaque);
  * RuntimeContext / ProducerResult / ProducerSpec carry ONLY the allowed fields;
  * the registry is empty and importing the foundation pulls in NO scheduler /
    producer / Telegram / Decision-Spine module.
"""
import ast
import inspect
import os
import tempfile
from dataclasses import fields

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext

from database import Base
import models  # noqa: F401  (registers tables)
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import RuntimeContext, ProducerResult
from services.advisory_runtime.registry import ProducerSpec, ADVISORY_PRODUCERS
import services.advisory_runtime.runtime as runtime_mod
import services.advisory_runtime.registry as registry_mod

REV = "advr1a2b3c4d01"
PRIOR = "ops1a2b3c4d01"
TABLE = "advisory_run"
EXPECTED_INDEXES = {
    "ix_advisory_run_user_producer_started",
    "ix_advisory_run_run_id",
    "ix_advisory_run_status",
}
FORBIDDEN_COUNTER_COLUMNS = {"listings_seen", "signals_created", "problems_detected"}


# ── A. migration / model ─────────────────────────────────────────────────────

def _current(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def _tables(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return set(sa.inspect(c).get_table_names())
    finally:
        eng.dispose()


def _indexes(sync_url, table):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return {ix["name"] for ix in sa.inspect(c).get_indexes(table)}
    finally:
        eng.dispose()


def test_advisory_run_migration_roundtrip(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "advisory_run_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    heads = ScriptDirectory.from_config(cfg).get_heads()
    # head advanced by later additive migrations (e.g. revenue diagnosis foundation);
    # REV below stays the advisory-run revision this test round-trips.
    assert heads == ["rsg1a2b3c4d01"], f"expected single head, got {heads}"

    command.upgrade(cfg, REV)
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)
    assert EXPECTED_INDEXES <= _indexes(sync_url, TABLE)

    command.downgrade(cfg, PRIOR)
    assert _current(sync_url) == PRIOR
    assert TABLE not in _tables(sync_url)

    command.upgrade(cfg, "head")
    assert _current(sync_url) == "rsg1a2b3c4d01"   # head advanced past advisory-run
    assert TABLE in _tables(sync_url)


def test_advisory_run_columns_no_typed_counters():
    cols = set(AdvisoryRun.__table__.columns.keys())
    expected = {
        "id", "run_id", "user_id", "producer_key", "started_at", "finished_at",
        "duration_ms", "status", "error", "triggered_by", "stats",
    }
    assert cols == expected, f"unexpected columns: {cols ^ expected}"
    assert not (cols & FORBIDDEN_COUNTER_COLUMNS), "typed counters must not exist (stats is opaque)"


# ── B. contracts ─────────────────────────────────────────────────────────────

def test_runtime_context_fields_exact():
    names = {f.name for f in fields(RuntimeContext)}
    assert names == {"db", "user_id", "now", "run_id", "logger", "triggered_by"}
    # explicitly forbidden contour-specific / config leakage
    for bad in ("config", "thresholds", "limits", "marketplace", "sku",
                "listing_id", "snapshot", "snapshots"):
        assert bad not in names


def test_producer_result_minimal():
    names = {f.name for f in fields(ProducerResult)}
    assert names == {"ok", "stats"}


def test_producer_spec_fields_exact():
    names = {f.name for f in fields(ProducerSpec)}
    assert names == {"key", "run", "cadence_seconds", "enabled"}
    for bad in ("scope", "max_units_per_run", "snapshot_builder", "thresholds"):
        assert bad not in names


# ── C. registry ──────────────────────────────────────────────────────────────

def test_registry_growth_disabled_legal_enabled():
    assert isinstance(ADVISORY_PRODUCERS, tuple)
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["growth"].enabled is True    # A13: data-derived thresholds → live
    assert by_key["legal"].enabled is True      # A8: first live-safe producer


# ── D. guard — foundation imports no scheduler / producer / Telegram / Spine ──

def _imported_modules(mod) -> set:
    tree = ast.parse(inspect.getsource(mod))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_foundation_imports_nothing_live():
    blob = " ".join(_imported_modules(runtime_mod) | _imported_modules(registry_mod)).lower()
    for forbidden in ("scheduler", "intelligence_loop", "telegram", "decision_feed",
                      "decision_outcome", "action_binding", "executor", "action_engine",
                      "audit_persist", "internal_source"):
        assert forbidden not in blob, f"foundation must not import {forbidden}"
