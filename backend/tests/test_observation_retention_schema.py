"""PULT-LAUNCH-2.5E-2B-1 — schema for observation retention (flag + index only, feature OFF).

Proves the ONLY things this slice adds: a default-False observation_retention_enabled flag (independent
of the two existing master switches), a new ix_price_obs_account_time(marketplace_account_id, fetched_at)
index on the price observation table (mirroring the promotion table's existing account-time index), and a
minimal additive migration. Plus a guard that NOTHING executes: no cleanup service, no DELETE against the
observation tables, no advisory lock, and the scheduler is untouched.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

import models  # noqa: F401 — register full metadata
from config import Settings, settings
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import MarketplacePromotionObservation as PO

HEAD = "tkv1a2b3c4d01"
PRIOR = "eco1a2b3c4d01"
_BACKEND = Path(__file__).resolve().parents[1]


# ── 5.1 CONFIG ───────────────────────────────────────────────────────────────────
def test_flag_default_false():
    assert settings.observation_retention_enabled is False


def test_flag_is_independent_of_the_two_master_switches():
    assert settings.api_data_sync_enabled is False
    assert settings.automation_enabled is False
    # turning the new flag on in a fresh config does NOT flip the other two
    s = Settings(observation_retention_enabled=True)
    assert s.observation_retention_enabled is True
    assert s.api_data_sync_enabled is False
    assert s.automation_enabled is False


# ── 5.2 MODEL ────────────────────────────────────────────────────────────────────
def _index_cols(model, name):
    for ix in model.__table__.indexes:
        if ix.name == name:
            return [c.name for c in ix.columns]
    return None


def test_price_account_time_index_exact():
    assert _index_cols(MPO, "ix_price_obs_account_time") == ["marketplace_account_id", "fetched_at"]


def test_promo_account_time_index_exists_and_not_duplicated():
    # the promotion table already had this index; it must still be exactly one, unchanged.
    assert _index_cols(PO, "ix_promo_obs_account_time") == ["marketplace_account_id", "fetched_at"]
    names = [ix.name for ix in PO.__table__.indexes]
    assert names.count("ix_promo_obs_account_time") == 1
    # the new price index name must NOT appear on the promotion table
    assert "ix_price_obs_account_time" not in names


# ── 5.3 MIGRATION (SQLite) ───────────────────────────────────────────────────────
def _cfg(monkeypatch, dbfile):
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    import db_migrations as dbm
    return dbm._alembic_config()


def _has_index(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone() is not None


def test_single_head():
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [HEAD]


def test_migration_roundtrip_nonempty(monkeypatch, tmp_path):
    dbfile = tmp_path / "rpa.db"
    cfg = _cfg(monkeypatch, dbfile)

    command.upgrade(cfg, PRIOR)                     # up to eco
    con = sqlite3.connect(str(dbfile))
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute(
        "INSERT INTO marketplace_price_observations "
        "(id,ingest_run_id,marketplace_account_id,marketplace_store_id,product_id,external_product_id,"
        " resolution_status,observation_kind,promotion_key,currency_status,seller_revenue_status,"
        " commission_base_status,subsidy_status,source,fetched_at,last_verified_at,missing_fields,"
        " created_at) VALUES ('P1','r','a','s',NULL,'E','unassigned','catalog','__none__','unknown',"
        " 'unknown','unknown','unknown','api','2026-05-01 10:00:00','2026-05-01 10:00:00','[]',"
        " '2026-05-01 10:00:00')")
    con.commit()
    con.close()

    command.upgrade(cfg, HEAD)                      # eco -> rpa
    con = sqlite3.connect(str(dbfile))
    try:
        assert _has_index(con, "ix_price_obs_account_time")            # created
        assert con.execute("SELECT external_product_id FROM marketplace_price_observations "
                           "WHERE id='P1'").fetchone()[0] == "E"       # existing row preserved
    finally:
        con.close()

    command.downgrade(cfg, PRIOR)                   # rpa -> eco
    con = sqlite3.connect(str(dbfile))
    try:
        assert not _has_index(con, "ix_price_obs_account_time")        # only the new index dropped
        assert _has_index(con, "ix_price_obs_series")                  # existing indexes kept
        assert _has_index(con, "ix_price_obs_latest")
        assert con.execute("SELECT COUNT(*) FROM marketplace_price_observations").fetchone()[0] == 1
    finally:
        con.close()

    command.upgrade(cfg, HEAD)                      # re-upgrade succeeds
    con = sqlite3.connect(str(dbfile))
    try:
        assert _has_index(con, "ix_price_obs_account_time")
    finally:
        con.close()


# ── 5.5 GUARD (nothing executes) ─────────────────────────────────────────────────
def _read(rel):
    return (_BACKEND / rel).read_text(encoding="utf-8")


# PULT-LAUNCH-2.5E-2B-2 added the sweep SERVICE; it lives in exactly this package and nothing else may
# delete from the observation tables or take the retention advisory lock.
_RETENTION_PKG = "services/marketplace/retention/"


def test_only_the_retention_package_deletes_or_locks():
    # The actual DELETE against the observation tables and the advisory lock live ONLY in the retention
    # package. The scheduler (2.5E-3B) may CALL run_observation_retention, but must not delete/lock itself.
    import glob
    offenders = []
    for path in glob.glob(str(_BACKEND / "**" / "*.py"), recursive=True):
        rel = os.path.relpath(path, _BACKEND).replace("\\", "/")
        if rel.startswith(("tests/", "models/")) or "alembic/versions/" in rel or rel.startswith(_RETENTION_PKG):
            continue
        low = open(path, encoding="utf-8").read().lower()
        if "delete from marketplace_price_observations" in low \
                or "delete from marketplace_promotion_observations" in low \
                or "pg_advisory" in low or "pg_try_advisory" in low:
            offenders.append(rel)
    assert offenders == [], offenders


def test_scheduler_wires_retention_via_one_tick_no_second_loop():
    # PULT-LAUNCH-2.5E-3B: the scheduler wires retention through a single tick + a tracked task, inside
    # the ONE existing loop — never a second scheduler, never an inline await of the sweep, never a
    # direct DELETE/advisory lock.
    sched = _read("tasks/scheduler.py")
    assert "_observation_retention_tick" in sched
    assert "run_observation_retention" in sched
    assert sched.count("while True") == 1
    low = sched.lower()
    assert "delete from marketplace_" not in low and "pg_advisory" not in low


def test_flag_read_only_by_retention_package_and_scheduler():
    # The retention flags gate ONLY the sweep service and the scheduler tick — no other module reads them
    # to start work, and there is no endpoint.
    import glob
    allowed = (_RETENTION_PKG, "tasks/scheduler.py")
    readers = []
    for path in glob.glob(str(_BACKEND / "**" / "*.py"), recursive=True):
        rel = os.path.relpath(path, _BACKEND).replace("\\", "/")
        if rel.startswith("tests/") or rel == "config.py" or "alembic/versions/" in rel \
                or rel.startswith(_RETENTION_PKG) or rel in allowed:
            continue
        if "observation_retention_enabled" in open(path, encoding="utf-8").read() \
                or "observation_retention_dry_run" in open(path, encoding="utf-8").read():
            readers.append(rel)
    assert readers == [], readers
