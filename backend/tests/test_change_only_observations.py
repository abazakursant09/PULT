"""PULT-LAUNCH-2.5E-1 — change-only writers + evidence fingerprint (feature OFF).

Two axes: (1) the pure evidence_fingerprint contract (canonical, versioned, order-independent, Decimal
scale, NULL≠0, ignores technical fields); (2) the change-only write behaviour of observe_price /
observe_promotion against an in-memory DB (first insert, unchanged no-row, the 24h last_verified rule,
a real change → a new version, the 100→90→100 three-change-point history, a NULL fingerprint forcing a
fresh insert, and the Yandex parent+child atomic write). Plus the schema guards (index composition,
nullability, SHA length) and the PostgreSQL EXPLAIN gate (BLOCKED_ENVIRONMENT when no PG is available —
SQLite EXPLAIN is never accepted as a substitute).

FK enforcement is intentionally left OFF here (default aiosqlite): these tests exercise the change-only
LOGIC with unassigned rows (product_id IS NULL), not tenant isolation, which its own suites cover.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 — register full metadata
from database import Base
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as PO, MarketplacePromotionStoreEvidence as SE)
from services.marketplace.ingest.change_only import (
    FINGERPRINT_VERSION, _maybe_bump, _to_utc, evidence_fingerprint, observe_price,
    observe_promotion, price_fingerprint, promo_fingerprint)

T0 = datetime(2026, 7, 28, 12, 0, 0)
_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── fingerprint payload helpers ─────────────────────────────────────────────────
def _pf(**over) -> dict:
    base = dict(promotion_id=None, promotion_type=None, participation_status=None,
                catalog_price=Decimal("2000"), buyer_price=Decimal("1500"), seller_promo_price=None,
                marketplace_subsidy=None, expected_seller_revenue=None, commission_base=None,
                provider_min_price=None, auto_action_enabled=None, club_buyer_price=None,
                currency="RUB", currency_status="proven", seller_revenue_status="unknown",
                commission_base_status="unknown", subsidy_status="unknown",
                provider_valid_from=None, provider_valid_to=None,
                provider_dataset="prices", missing_fields=[])
    base.update(over)
    return base


def _promf(**over) -> dict:
    base = dict(provider_status="PARTIALLY_AUTO", participation_status="active",
                auto_participation=True, attribution_status="exact_stores",
                pre_promo_price=Decimal("2000"), promo_buyer_price=Decimal("1500"),
                promo_max_price=None, currency="RUB", currency_status="proven",
                promotion_start_at=None, promotion_end_at=None, missing_fields=[])
    base.update(over)
    return base


async def _obs_price(db, now, *, product_id=None, **over):
    await observe_price(db, run_id=str(uuid.uuid4()), account_id="a", store_id="s", ext="E",
                        observation_kind="catalog", promotion_key="__none__", product_id=product_id,
                        source="api", now=now, fields=_pf(**over))
    await db.commit()


async def _obs_promo(db, now, child_evidence, *, product_id=None, **over):
    await observe_promotion(db, run_id=str(uuid.uuid4()), account_id="a", offer_id="OF", promo_id="PR",
                            product_id=product_id, now=now, fields=_promf(**over),
                            child_evidence=child_evidence)
    await db.commit()


def _pcount(db):
    return _run(db.execute(select(func.count()).select_from(MPO))).scalar_one()


def _parent_count(db):
    return _run(db.execute(select(func.count()).select_from(PO))).scalar_one()


def _child_count(db):
    return _run(db.execute(select(func.count()).select_from(SE))).scalar_one()


# ══ FINGERPRINT ══════════════════════════════════════════════════════════════════
def test_fingerprint_is_sha256_hex():
    fp = evidence_fingerprint({"x": "y"})
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_version_is_in_payload():
    payload = {"a": "x"}
    canonical = {"fingerprint_version": FINGERPRINT_VERSION, "fields": {"a": "x"}}
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    assert evidence_fingerprint(payload) == expected
    # a different version namespace would change the hash (proves the version is IN the payload)
    other = hashlib.sha256(
        json.dumps({"fingerprint_version": FINGERPRINT_VERSION + 1, "fields": {"a": "x"}},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    assert evidence_fingerprint(payload) != other


def test_key_order_does_not_matter():
    assert evidence_fingerprint({"a": 1, "b": 2}) == evidence_fingerprint({"b": 2, "a": 1})


def test_decimal_100_equals_100_00():
    assert evidence_fingerprint({"p": Decimal("100")}) == evidence_fingerprint({"p": Decimal("100.00")})


def test_null_differs_from_zero():
    assert evidence_fingerprint({"p": None}) != evidence_fingerprint({"p": Decimal("0")})
    assert evidence_fingerprint({"p": None}) != evidence_fingerprint({"p": Decimal("0.00")})


def test_missing_fields_order_ignored():
    a = price_fingerprint(resolution_status="unassigned", product_id=None, observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(missing_fields=["price", "old_price"]))
    b = price_fingerprint(resolution_status="unassigned", product_id=None, observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(missing_fields=["old_price", "price"]))
    assert a == b


def test_any_semantic_change_flips_fingerprint():
    base = price_fingerprint(resolution_status="unassigned", product_id=None, observation_kind="catalog",
                             promotion_key="__none__", fields=_pf())
    changed = price_fingerprint(resolution_status="unassigned", product_id=None,
                                observation_kind="catalog", promotion_key="__none__",
                                fields=_pf(buyer_price=Decimal("1400")))
    assert base != changed


def test_technical_fields_do_not_affect_fingerprint():
    # ingest_run_id / fetched_at / an unrelated UUID passed alongside the semantic fields are ignored:
    # price_fingerprint only reads the SEMANTIC set + resolution/product_id/kind/promotion_key.
    clean = price_fingerprint(resolution_status="unassigned", product_id=None,
                              observation_kind="catalog", promotion_key="__none__", fields=_pf())
    noisy = price_fingerprint(resolution_status="unassigned", product_id=None,
                              observation_kind="catalog", promotion_key="__none__",
                              fields=_pf(ingest_run_id="RUN", fetched_at=T0, id=str(uuid.uuid4())))
    assert clean == noisy


def test_product_id_is_in_fingerprint():
    # NULL vs A vs B are DIFFERENT evidence; two distinct internal UUIDs differ.
    none = price_fingerprint(resolution_status="unassigned", product_id=None,
                             observation_kind="catalog", promotion_key="__none__", fields=_pf())
    a = price_fingerprint(resolution_status="resolved", product_id="pA",
                          observation_kind="catalog", promotion_key="__none__", fields=_pf())
    b = price_fingerprint(resolution_status="resolved", product_id="pB",
                          observation_kind="catalog", promotion_key="__none__", fields=_pf())
    assert none != a and a != b and none != b


def test_promo_child_order_ignored():
    a = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c1", "mapped", "stA"], ["c2", "unmapped", None]])
    b = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c2", "unmapped", None], ["c1", "mapped", "stA"]])
    assert a == b


def test_promo_child_mapping_change_flips():
    a = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c1", "unmapped", None]])
    b = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c1", "mapped", "stA"]])
    assert a != b


def test_promo_child_store_binding_is_in_fingerprint():
    # unmapped vs mapped→A vs mapped→B are three different evidences (marketplace_store_id in the hash).
    unm = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                            child_set=[["c1", "unmapped", None]])
    a = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c1", "mapped", "stA"]])
    b = promo_fingerprint(resolution_status="unassigned", product_id=None, fields=_promf(),
                          child_set=[["c1", "mapped", "stB"]])
    assert unm != a and a != b and unm != b


def test_utc_aware_and_z_same_fingerprint():
    from datetime import timezone as _tz, timedelta as _td
    msk = datetime(2026, 7, 28, 12, 0, 0, tzinfo=_tz(_td(hours=3)))   # 12:00+03:00
    utc = datetime(2026, 7, 28, 9, 0, 0, tzinfo=_tz.utc)             # 09:00Z — same instant
    a = price_fingerprint(resolution_status="unassigned", product_id=None, observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(provider_valid_from=msk))
    b = price_fingerprint(resolution_status="unassigned", product_id=None, observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(provider_valid_from=utc))
    assert a == b


# ══ observe_price — change-only ══════════════════════════════════════════════════
def test_first_observation_inserts():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    assert _pcount(db) == 1
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.fetched_at == T0 and row.last_verified_at == T0 and len(row.evidence_fingerprint) == 64


def test_unchanged_repeat_writes_no_row_and_no_bump_under_24h():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    _run(_obs_price(db, T0 + timedelta(hours=23, minutes=59)))     # < 24h
    assert _pcount(db) == 1
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.last_verified_at == T0                              # NOT bumped


def test_unchanged_repeat_bumps_last_verified_after_24h_only():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    _run(_obs_price(db, T0 + timedelta(hours=25)))                 # ≥ 24h
    assert _pcount(db) == 1                                        # still no new row
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.last_verified_at == T0 + timedelta(hours=25)        # last_verified advanced
    assert row.fetched_at == T0                                    # first-observed pinned


def test_change_writes_new_version():
    db = _run(_new_db())
    _run(_obs_price(db, T0, buyer_price=Decimal("1500")))
    _run(_obs_price(db, T0 + timedelta(hours=1), buyer_price=Decimal("1400")))
    assert _pcount(db) == 2


def test_100_90_100_keeps_three_change_points():
    db = _run(_new_db())
    _run(_obs_price(db, T0, buyer_price=Decimal("100")))
    _run(_obs_price(db, T0 + timedelta(hours=1), buyer_price=Decimal("90")))
    _run(_obs_price(db, T0 + timedelta(hours=2), buyer_price=Decimal("100")))
    # the third 100 is compared ONLY against the immediate 90, never the historical first 100.
    assert _pcount(db) == 3
    prices = sorted(r.buyer_price for r in _run(db.execute(select(MPO))).scalars().all())
    assert prices == [Decimal("90"), Decimal("100"), Decimal("100")]


def test_null_fingerprint_latest_forces_insert():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    row = _run(db.execute(select(MPO))).scalars().first()
    row.evidence_fingerprint = None                               # a hypothetical pre-fingerprint row
    _run(db.commit())
    _run(_obs_price(db, T0 + timedelta(hours=1)))                 # identical evidence
    assert _pcount(db) == 2                                       # NULL latest → never a false dedupe


# ══ observe_promotion — change-only parent + children ════════════════════════════
def test_promo_first_insert_writes_parent_and_children():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1"), ("c2", None)]))
    assert _parent_count(db) == 1 and _child_count(db) == 2


def test_promo_unchanged_child_order_no_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1"), ("c2", None)]))
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c2", None), ("c1", "st1")]))
    assert _parent_count(db) == 1 and _child_count(db) == 2       # no recreate, order-invariant


def test_promo_campaign_set_change_writes_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1")]))
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c1", "st1"), ("c2", None)]))
    assert _parent_count(db) == 2


def test_promo_mapped_to_unmapped_writes_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1")]))       # mapped
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c1", None)]))   # unmapped
    assert _parent_count(db) == 2


def test_observe_price_product_binding_versions():
    # Same series (store, ext, kind, promo_key, source); product re-mapping alone must version.
    db = _run(_new_db())
    _run(_obs_price(db, T0, product_id=None))                       # unassigned
    _run(_obs_price(db, T0 + timedelta(hours=1), product_id="pA"))  # NULL→A  → new
    _run(_obs_price(db, T0 + timedelta(hours=2), product_id="pA"))  # A→A     → none
    _run(_obs_price(db, T0 + timedelta(hours=3), product_id="pB"))  # A→B     → new
    assert _pcount(db) == 3


def test_observe_promo_store_binding_versions():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", None)]))                         # unmapped
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c1", "stA")]))   # →A  → new
    _run(_obs_promo(db, T0 + timedelta(hours=2), child_evidence=[("c1", "stA")]))   # A→A → none
    _run(_obs_promo(db, T0 + timedelta(hours=3), child_evidence=[("c1", "stB")]))   # A→B → new
    assert _parent_count(db) == 3


def test_child_store_uuid_not_exposed_in_fingerprint():
    fp = promo_fingerprint(resolution_status="resolved", product_id="pA", fields=_promf(),
                           child_set=[["c1", "mapped", "store-uuid-secret-123"]])
    assert "store-uuid-secret-123" not in fp     # only the SHA-256 hash leaves the writer
    assert "pA" not in fp and len(fp) == 64


# ══ UTC normalization (blocker 4) ════════════════════════════════════════════════
def test_to_utc_normalizes_aware_and_treats_naive_as_utc():
    from datetime import timezone as _tz, timedelta as _td
    assert _to_utc(datetime(2026, 7, 28, 12, 0, tzinfo=_tz(_td(hours=3)))) == datetime(2026, 7, 28, 9, 0)
    assert _to_utc(datetime(2026, 7, 28, 9, 0)) == datetime(2026, 7, 28, 9, 0)     # naive == UTC
    assert _to_utc(None) is None


def test_maybe_bump_24h_boundary_across_timezones():
    from datetime import timezone as _tz, timedelta as _td

    class _Row:
        pass
    # last_verified stored AWARE (as PostgreSQL returns it): 12:00+03:00 == 09:00Z on the 28th.
    r = _Row()
    r.last_verified_at = datetime(2026, 7, 28, 12, 0, 0, tzinfo=_tz(_td(hours=3)))
    _maybe_bump(r, datetime(2026, 7, 29, 8, 59, 0))          # 23:59 later (UTC) → NO bump
    assert r.last_verified_at == datetime(2026, 7, 28, 12, 0, 0, tzinfo=_tz(_td(hours=3)))
    _maybe_bump(r, datetime(2026, 7, 29, 9, 0, 0))           # exactly 24h later → bump
    assert r.last_verified_at == datetime(2026, 7, 29, 9, 0, 0)


# ══ MIGRATION no-default (blocker 1) ═════════════════════════════════════════════
def test_migration_last_verified_notnull_no_default(monkeypatch, tmp_path):
    import sqlite3
    from alembic import command
    dbfile = tmp_path / "eco_default.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    import db_migrations as dbm
    command.upgrade(dbm._alembic_config(), "eco1a2b3c4d01")
    c = sqlite3.connect(str(dbfile))
    try:
        for t in ("marketplace_price_observations", "marketplace_promotion_observations"):
            info = {r[1]: (r[3], r[4]) for r in c.execute(f"PRAGMA table_info({t})")}  # name:(notnull,dflt)
            assert info["last_verified_at"] == (1, None), (t, info["last_verified_at"])   # NOT NULL, no default
    finally:
        c.close()


# ── seed helpers for the NON-EMPTY migration regression ─────────────────────────
_SEED = """
INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x');
INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP);
INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status)
  VALUES('accY','ws1','yandex','verified');
INSERT INTO marketplace_stores(id,marketplace_account_id,marketplace,store_key,external_store_id,label,source,status,created_at,updated_at)
  VALUES('sY','accY','yandex','sY','c1','S','api','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id)
  VALUES('pY','u1','N','yandex','OF-1','accY');
INSERT INTO product_placements(id,product_id,marketplace_store_id,marketplace_account_id,status,source,first_seen_at,last_seen_at)
  VALUES('pp','pY','sY','accY','active','api',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
-- MPO P1: resolved (pY placed in sY), fetched_at A
INSERT INTO marketplace_price_observations
  (id,ingest_run_id,marketplace_account_id,marketplace_store_id,product_id,external_product_id,
   resolution_status,observation_kind,promotion_key,currency_status,seller_revenue_status,
   commission_base_status,subsidy_status,source,fetched_at,missing_fields,created_at)
  VALUES('P1','r','accY','sY','pY','E','resolved','catalog','__none__','unknown','unknown',
         'unknown','unknown','api','2026-05-01 10:00:00','[]','2026-05-01 10:00:00');
-- PO Q1: unassigned, fetched_at B
INSERT INTO marketplace_promotion_observations
  (id,ingest_run_id,marketplace_account_id,marketplace,product_id,external_product_id,resolution_status,
   promotion_id,promotion_type,provider_status,participation_status,attribution_status,currency_status,
   source,provider_dataset,fetched_at,missing_fields,created_at)
  VALUES('Q1','r','accY','yandex',NULL,'OF','unassigned','PR1','yandex_promo','AUTO','active',
         'account_wide','unknown','api','promos','2026-06-15 08:30:00','[]','2026-06-15 08:30:00');
-- PO Q2: PARTIALLY_AUTO exact_stores + a child, fetched_at C
INSERT INTO marketplace_promotion_observations
  (id,ingest_run_id,marketplace_account_id,marketplace,product_id,external_product_id,resolution_status,
   promotion_id,promotion_type,provider_status,participation_status,attribution_status,currency_status,
   source,provider_dataset,fetched_at,missing_fields,created_at)
  VALUES('Q2','r','accY','yandex',NULL,'OF','unassigned','PR2','yandex_promo','PARTIALLY_AUTO','active',
         'exact_stores','unknown','api','promos','2026-07-01 00:00:00','[]','2026-07-01 00:00:00');
INSERT INTO marketplace_promotion_store_evidence
  (id,promotion_observation_id,marketplace_account_id,external_store_id,marketplace_store_id,mapping_status,created_at)
  VALUES('SE1','Q2','accY','c1','sY','mapped',CURRENT_TIMESTAMP);
"""

_MPO_COLS = ("id,ingest_run_id,marketplace_account_id,marketplace_store_id,product_id,external_product_id,"
             "resolution_status,observation_kind,promotion_key,currency_status,seller_revenue_status,"
             "commission_base_status,subsidy_status,source,fetched_at,last_verified_at,missing_fields,created_at")
_MPO_VALS = ("'{id}','{run}','accY','sY',{pid},'{ext}','{res}','catalog','__none__','unknown','unknown',"
             "'unknown','unknown','api','2026-08-01 00:00:00',{lv},'[]','2026-08-01 00:00:00'{extra}")


def test_migration_nonempty_sqlite_backfill_preserve_and_constraints(monkeypatch, tmp_path):
    """PULT-LAUNCH-2.5E-1 (final): the SQLite upgrade must migrate a NON-EMPTY table — backfill existing
    rows, end NOT NULL with no default, and preserve every column / FK / UNIQUE / CHECK / index / row."""
    import sqlite3
    from alembic import command
    dbfile = tmp_path / "eco_nonempty.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, "ypo1a2b3c4d01")
    con = sqlite3.connect(str(dbfile))
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(_SEED)
    con.commit()
    con.close()

    # ── the migration under test ──
    command.upgrade(cfg, "eco1a2b3c4d01")

    con = sqlite3.connect(str(dbfile))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        # backfill last_verified_at = fetched_at (distinct per row); fingerprint of old rows is NULL
        p = con.execute("SELECT fetched_at, last_verified_at, evidence_fingerprint "
                        "FROM marketplace_price_observations WHERE id='P1'").fetchone()
        assert p[0] == p[1] == "2026-05-01 10:00:00" and p[2] is None
        q = con.execute("SELECT fetched_at, last_verified_at, evidence_fingerprint "
                        "FROM marketplace_promotion_observations WHERE id='Q1'").fetchone()
        assert q[0] == q[1] == "2026-06-15 08:30:00" and q[2] is None
        # all rows preserved (P1 + Q1 + Q2 + the child SE1) — nothing lost in the rebuild
        assert con.execute("SELECT COUNT(*) FROM marketplace_price_observations").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM marketplace_promotion_observations").fetchone()[0] == 2
        assert con.execute("SELECT promotion_observation_id FROM marketplace_promotion_store_evidence "
                           "WHERE id='SE1'").fetchone()[0] == "Q2"   # child FK survived the parent rebuild

        # NOT NULL, no default (PRAGMA)
        for t in ("marketplace_price_observations", "marketplace_promotion_observations"):
            info = {r[1]: (r[3], r[4]) for r in con.execute(f"PRAGMA table_info({t})")}
            assert info["last_verified_at"] == (1, None)
        # new series indexes exist
        for name in ("ix_price_obs_series", "ix_promo_obs_series"):
            assert con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                               (name,)).fetchone() is not None

        def _mpo(**kw):
            for k, v in (("id", str(uuid.uuid4())), ("run", str(uuid.uuid4())), ("pid", "NULL"),
                         ("ext", "Z"), ("res", "unassigned"), ("lv", "'2026-08-01 00:00:00'"),
                         ("extra", "")):
                kw.setdefault(k, v)
            con.execute(f"INSERT INTO marketplace_price_observations({_MPO_COLS}) "
                        f"VALUES({_MPO_VALS.format(**kw)})")

        # last_verified_at is required — omitting it is an IntegrityError, not a sentinel
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO marketplace_price_observations "
                        "(id,ingest_run_id,marketplace_account_id,marketplace_store_id,external_product_id,"
                        " resolution_status,observation_kind,promotion_key,currency_status,"
                        " seller_revenue_status,commission_base_status,subsidy_status,source,fetched_at,"
                        " missing_fields,created_at) VALUES('X','x','accY','sY','Z','unassigned','catalog',"
                        " '__none__','unknown','unknown','unknown','unknown','api','2026-08-01 00:00:00',"
                        " '[]','2026-08-01 00:00:00')")
        # CHECK still blocks: resolved with product_id NULL
        with pytest.raises(sqlite3.IntegrityError):
            _mpo(res="resolved", pid="NULL")
        # FK still blocks: resolved with an unplaced product
        with pytest.raises(sqlite3.IntegrityError):
            _mpo(res="resolved", pid="'pGHOST'")
        # UNIQUE still blocks: a duplicate run-key of the existing P1 row
        with pytest.raises(sqlite3.IntegrityError):
            _mpo(id="dup", run="r", ext="E")                       # same (store, ext, kind, promo_key, source, run)
        # club CHECK survived as a WORKING COLUMN-INLINE constraint (negative rejected)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(f"INSERT INTO marketplace_price_observations({_MPO_COLS},club_buyer_price) "
                        f"VALUES({_MPO_VALS.format(id='cn', run='cn', pid='NULL', ext='CN', res='unassigned', lv=chr(39)+'2026-08-01 00:00:00'+chr(39), extra='')},-1)")
    finally:
        con.close()

    # downgrade removes ONLY the new fields, keeps the old data
    command.downgrade(cfg, "ypo1a2b3c4d01")
    con = sqlite3.connect(str(dbfile))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(marketplace_price_observations)")}
        assert "last_verified_at" not in cols and "evidence_fingerprint" not in cols
        assert con.execute("SELECT external_product_id FROM marketplace_price_observations "
                           "WHERE id='P1'").fetchone()[0] == "E"      # old row + old column intact
        assert con.execute("SELECT COUNT(*) FROM marketplace_promotion_store_evidence").fetchone()[0] == 1
    finally:
        con.close()
    command.upgrade(cfg, "eco1a2b3c4d01")                            # re-upgrade succeeds


# ══ SCHEMA GUARDS ════════════════════════════════════════════════════════════════
def test_price_series_index_exact_composition():
    idx = {i.name: [c.name for c in i.columns] for i in MPO.__table__.indexes}
    assert idx["ix_price_obs_series"] == [
        "marketplace_store_id", "external_product_id", "observation_kind",
        "promotion_key", "source", "fetched_at"]


def test_promo_series_index_exact_composition():
    idx = {i.name: [c.name for c in i.columns] for i in PO.__table__.indexes}
    assert idx["ix_promo_obs_series"] == [
        "marketplace_account_id", "external_product_id", "promotion_id", "source", "fetched_at"]


def test_last_verified_and_fingerprint_are_not_indexed():
    for model in (MPO, PO):
        for i in model.__table__.indexes:
            cols = [c.name for c in i.columns]
            assert "last_verified_at" not in cols, i.name
            assert "evidence_fingerprint" not in cols, i.name


def test_column_nullability_length_and_no_default():
    for model in (MPO, PO):
        lv = model.__table__.c.last_verified_at
        assert lv.nullable is False
        assert lv.server_default is None and lv.default is None       # no default (parity)
        assert model.__table__.c.evidence_fingerprint.nullable is True
        assert model.__table__.c.evidence_fingerprint.type.length == 64


# ══ REAL PostgreSQL: Alembic migration + EXPLAIN GATE (SQLite EXPLAIN never substituted) ══════════════
# A MANDATORY gate, run in the dedicated `postgres-explain` CI job (PostgreSQL service container). It
# applies the REAL Alembic migration chain up to ypo1a2b3c4d01, seeds NON-EMPTY parent tables, then
# runs the change-only migration eco1a2b3c4d01 and proves on PostgreSQL: backfill (last_verified_at =
# fetched_at), NOT NULL, NO column default — and finally that the latest-of-series lookup uses
# ix_price_obs_series / ix_promo_obs_series (incl. product_id IS NULL). No create_all: this exercises
# the migration's PostgreSQL branch. A skip (no PostgreSQL) is NOT a passing state for 2.5E-1; a SQLite
# EXPLAIN is never accepted. If the old-migration chain itself fails on PostgreSQL, that is reported as
# a failure — never masked by falling back to create_all.
def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_alembic_url():
    # Alembic's env uses an ASYNC driver; derive an asyncpg URL from the sync one if not given.
    explicit = os.environ.get("PULT_TEST_PG_ALEMBIC_URL")
    if explicit:
        return explicit
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def test_pg_alembic_migration_nonempty_and_explain(monkeypatch):
    sync_url = _pg_sync_url()
    if not sync_url or not sync_url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL available (PULT_TEST_PG_URL unset). A SQLite "
                    "EXPLAIN is NOT accepted; the mandatory proof runs in the 'postgres-explain' CI job.")
    import sqlalchemy as sa
    from alembic import command
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    eng = sa.create_engine(sync_url)
    try:
        # clean slate so the real migration chain owns the schema (never create_all)
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")

        # ── REAL migration chain up to the pre-change-only head ──
        command.upgrade(cfg, "ypo1a2b3c4d01")

        # seed NON-EMPTY parent tables with distinct fetched_at (FK triggers off; synthetic rows)
        with eng.begin() as c:
            c.exec_driver_sql("SET session_replication_role = replica")
            c.exec_driver_sql(
                "INSERT INTO marketplace_price_observations "
                "(id,ingest_run_id,marketplace_account_id,marketplace_store_id,product_id,"
                " external_product_id,resolution_status,observation_kind,promotion_key,currency_status,"
                " seller_revenue_status,commission_base_status,subsidy_status,source,fetched_at,"
                " missing_fields,created_at) VALUES "
                "('P1','r','a','s',NULL,'E','unassigned','catalog','__none__','unknown','unknown',"
                " 'unknown','unknown','api','2026-05-01 10:00:00','[]','2026-05-01 10:00:00')")
            c.exec_driver_sql(
                "INSERT INTO marketplace_promotion_observations "
                "(id,ingest_run_id,marketplace_account_id,marketplace,product_id,external_product_id,"
                " resolution_status,promotion_id,promotion_type,provider_status,participation_status,"
                " attribution_status,currency_status,source,provider_dataset,fetched_at,missing_fields,"
                " created_at) VALUES ('Q1','r','a','yandex',NULL,'OF','unassigned','PR','yandex_promo',"
                " 'AUTO','active','account_wide','unknown','api','promos','2026-06-15 08:30:00','[]',"
                " '2026-06-15 08:30:00')")

        # ── the migration under test, on PostgreSQL ──
        command.upgrade(cfg, "eco1a2b3c4d01")

        with eng.connect() as c:
            # backfill on the pre-existing rows
            assert c.exec_driver_sql("SELECT last_verified_at = fetched_at AND evidence_fingerprint IS NULL "
                                     "FROM marketplace_price_observations WHERE id='P1'").scalar() is True
            assert c.exec_driver_sql("SELECT last_verified_at = fetched_at AND evidence_fingerprint IS NULL "
                                     "FROM marketplace_promotion_observations WHERE id='Q1'").scalar() is True
            # NOT NULL + NO default (information_schema)
            for t in ("marketplace_price_observations", "marketplace_promotion_observations"):
                nn, dflt = c.exec_driver_sql(
                    "SELECT is_nullable, column_default FROM information_schema.columns "
                    f"WHERE table_name='{t}' AND column_name='last_verified_at'").fetchone()
                assert nn == "NO" and dflt is None, (t, nn, dflt)

        # ── seed for planner selectivity, ANALYZE, then EXPLAIN ──
        with eng.begin() as c:
            c.exec_driver_sql("SET session_replication_role = replica")
            price_rows, promo_rows = [], []
            for i in range(4000):
                price_rows.append({"id": f"p{i}", "run": f"r{i}", "ext": f"E{i}",
                                   "fa": f"2026-01-01 00:00:{i % 60:02d}"})
                promo_rows.append({"id": f"q{i}", "run": f"r{i}", "ext": f"OF{i}", "pr": f"PR{i}",
                                   "fa": f"2026-01-01 00:00:{i % 60:02d}"})
            for j in range(300):     # target series: many change-points, product_id NULL (unassigned)
                price_rows.append({"id": f"pt{j}", "run": f"rt{j}", "ext": "E-TARGET",
                                   "fa": f"2026-02-01 00:{j // 60:02d}:{j % 60:02d}"})
                promo_rows.append({"id": f"qt{j}", "run": f"rt{j}", "ext": "OF-TARGET", "pr": "PR-T",
                                   "fa": f"2026-02-01 00:{j // 60:02d}:{j % 60:02d}"})
            c.exec_driver_sql(
                "INSERT INTO marketplace_price_observations "
                "(id,ingest_run_id,marketplace_account_id,marketplace_store_id,product_id,"
                " external_product_id,resolution_status,observation_kind,promotion_key,currency_status,"
                " seller_revenue_status,commission_base_status,subsidy_status,source,fetched_at,"
                " last_verified_at,missing_fields,created_at) VALUES "
                "(%(id)s,%(run)s,'a','s',NULL,%(ext)s,'unassigned','catalog','__none__','unknown',"
                " 'unknown','unknown','unknown','api',%(fa)s,%(fa)s,'[]',%(fa)s)", price_rows)
            c.exec_driver_sql(
                "INSERT INTO marketplace_promotion_observations "
                "(id,ingest_run_id,marketplace_account_id,marketplace,product_id,external_product_id,"
                " resolution_status,promotion_id,promotion_type,provider_status,participation_status,"
                " attribution_status,currency_status,source,provider_dataset,fetched_at,last_verified_at,"
                " missing_fields,created_at) VALUES "
                "(%(id)s,%(run)s,'a','yandex',NULL,%(ext)s,'unassigned',%(pr)s,'yandex_promo','AUTO',"
                " 'active','account_wide','unknown','api','promos',%(fa)s,%(fa)s,'[]',%(fa)s)", promo_rows)
            c.exec_driver_sql("ANALYZE marketplace_price_observations")
            c.exec_driver_sql("ANALYZE marketplace_promotion_observations")

        with eng.connect() as c:
            price_plan = "\n".join(str(r[0]) for r in c.exec_driver_sql(
                "EXPLAIN SELECT * FROM marketplace_price_observations "
                "WHERE marketplace_store_id='s' AND external_product_id='E-TARGET' "
                "AND observation_kind='catalog' AND promotion_key='__none__' AND source='api' "
                "ORDER BY fetched_at DESC, created_at DESC, id DESC LIMIT 1").fetchall())
            promo_plan = "\n".join(str(r[0]) for r in c.exec_driver_sql(
                "EXPLAIN SELECT * FROM marketplace_promotion_observations "
                "WHERE marketplace_account_id='a' AND external_product_id='OF-TARGET' "
                "AND promotion_id='PR-T' AND source='api' "
                "ORDER BY fetched_at DESC, created_at DESC, id DESC LIMIT 1").fetchall())
        assert "ix_price_obs_series" in price_plan, price_plan
        assert "ix_promo_obs_series" in promo_plan, promo_plan
    finally:
        eng.dispose()
