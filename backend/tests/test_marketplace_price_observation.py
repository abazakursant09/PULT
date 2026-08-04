"""PULT-LAUNCH-2.5C — DB guarantees for MarketplacePriceObservation (schema/provenance, feature OFF).

Proven against SQLite with foreign keys ON (the enforcement PostgreSQL gives in production): tenant
isolation via composite FKs (no cross-account / other-store-same-account product), the
resolution↔product_id CHECK that closes the MATCH-SIMPLE hole, the observation/participation matrix,
proof-status honesty (CSV/manual can never be provider_explicit/proven), currency format, money
non-negativity, run-idempotency that never bars a new price, and — because SQLite ignores VARCHAR
length while PostgreSQL enforces it — an explicit enum-length guard. Plus single head and a source
guard proving NO ingest/runtime/scheduler writer exists in this slice (retention pre-enable gate).
"""
import glob
import os
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from alembic.config import Config
from alembic.script import ScriptDirectory

import models  # noqa: F401 — register full metadata
from database import Base
from models.marketplace_price_observation import (
    MarketplacePriceObservation as MPO,
    RESOLUTION_STATUSES, OBSERVATION_KINDS, PROMOTION_TYPES, PARTICIPATION_STATUSES,
    CURRENCY_STATUSES, PROVIDER_PROOF_STATUSES, SUBSIDY_STATUSES, SOURCES,
)

T = MPO.__table__
NOW = datetime(2026, 7, 28, 12, 0, 0)


def _fk_engine():
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _fk(dbapi_con, _rec):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    return eng


@pytest.fixture
def conn():
    eng = _fk_engine()
    c = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(c)
    c.execute(text("INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x')"))
    c.execute(text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP)"))
    for acc, mp in (("accW", "wildberries"), ("accO", "ozon"), ("accY", "yandex")):
        c.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                       "VALUES(:a,'ws1',:m,'unverified')"), {"a": acc, "m": mp})
    _store(c, "s1", "accW", "wildberries", "primary")
    _store(c, "s2", "accO", "ozon", "primary")
    _store(c, "sy1", "accY", "yandex", "ky1")
    _store(c, "sy2", "accY", "yandex", "ky2")     # second store of the SAME account
    _product(c, "p1", "accW")
    _product(c, "pO", "accO", mp="ozon")
    _product(c, "pY", "accY", mp="yandex")
    _placement(c, "pp1", "p1", "s1", "accW")      # p1 placed in s1
    _placement(c, "ppO", "pO", "s2", "accO")      # pO placed in the OTHER account's store
    _placement(c, "ppY", "pY", "sy1", "accY")     # pY placed in sy1 only (NOT sy2)
    yield c
    c.close()


def _store(c, sid, acc, mp, key):
    c.execute(text(
        "INSERT INTO marketplace_stores"
        "(id,marketplace_account_id,marketplace,store_key,label,source,status,created_at,updated_at) "
        "VALUES(:id,:a,:m,:k,'S','manual','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": sid, "a": acc, "m": mp, "k": key})


def _product(c, pid, acc, mp="wildberries", sku="SKU"):
    c.execute(text(
        "INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id) "
        "VALUES(:id,'u1','N',:mp,:sku,:acc)"), {"id": pid, "mp": mp, "sku": pid, "acc": acc})


def _placement(c, ppid, pid, sid, acc):
    c.execute(text(
        "INSERT INTO product_placements"
        "(id,product_id,marketplace_store_id,marketplace_account_id,status,source,first_seen_at,last_seen_at) "
        "VALUES(:id,:p,:s,:a,'active','csv',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": ppid, "p": pid, "s": sid, "a": acc})


def _row(**over):
    """A valid RESOLVED CATALOG observation (source=csv), overridable per test."""
    base = dict(
        ingest_run_id="run1", marketplace_account_id="accW", marketplace_store_id="s1",
        product_id="p1", external_product_id="EXT1", resolution_status="resolved",
        observation_kind="catalog", promotion_id=None, promotion_key="__none__",
        promotion_type=None, participation_status=None,
        catalog_price=Decimal("1000.00"), buyer_price=None, seller_promo_price=None,
        marketplace_subsidy=None, expected_seller_revenue=None, commission_base=None,
        currency=None, currency_status="unknown", seller_revenue_status="unknown",
        commission_base_status="unknown", subsidy_status="unknown",
        source="csv", provider_dataset=None, external_row_id=None,
        provider_valid_from=None, provider_valid_to=None, fetched_at=NOW,
        last_verified_at=NOW, missing_fields=[], created_at=NOW)
    base.update(over)
    base.setdefault("id", str(uuid.uuid4()))
    return base


def ins(conn, **over):
    conn.execute(T.insert().values(**_row(**over)))


def _count(conn):
    return conn.execute(sa.select(sa.func.count()).select_from(T)).scalar()


# ── TENANT ────────────────────────────────────────────────────────────────────
def test_valid_resolved_observation(conn):
    ins(conn)
    assert _count(conn) == 1


def test_cross_account_store_blocked(conn):
    with pytest.raises(IntegrityError):   # store s1 belongs to accW, not accO
        ins(conn, marketplace_account_id="accO", resolution_status="unassigned", product_id=None)


def test_cross_account_product_blocked(conn):
    with pytest.raises(IntegrityError):   # pO is placed in accO's store, not s1
        ins(conn, product_id="pO")


def test_other_store_same_account_product_blocked(conn):
    with pytest.raises(IntegrityError):   # pY placed in sy1, not sy2 (same account accY)
        ins(conn, marketplace_account_id="accY", marketplace_store_id="sy2", product_id="pY")


def test_resolved_without_product_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, resolution_status="resolved", product_id=None)


def test_unassigned_with_product_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, resolution_status="unassigned", product_id="p1")


def test_unassigned_without_product_allowed(conn):
    ins(conn, resolution_status="unassigned", product_id=None)
    assert _count(conn) == 1


def test_last_verified_at_is_required(conn):
    # PULT-LAUNCH-2.5E-1: last_verified_at is NOT NULL with no default → omitting it is an IntegrityError,
    # never a silent sentinel date.
    values = {k: v for k, v in _row().items() if k != "last_verified_at"}
    with pytest.raises(IntegrityError):
        conn.execute(T.insert().values(**values))


# ── LENGTHS (PostgreSQL VARCHAR guard; SQLite does not enforce) ─────────────────
def test_enum_values_fit_column_length():
    mapping = {
        "resolution_status": RESOLUTION_STATUSES, "observation_kind": OBSERVATION_KINDS,
        "promotion_type": PROMOTION_TYPES, "participation_status": PARTICIPATION_STATUSES,
        "currency_status": CURRENCY_STATUSES, "seller_revenue_status": PROVIDER_PROOF_STATUSES,
        "commission_base_status": PROVIDER_PROOF_STATUSES, "subsidy_status": SUBSIDY_STATUSES,
        "source": SOURCES,
    }
    for col, vocab in mapping.items():
        length = T.c[col].type.length
        longest = max(len(v) for v in vocab)
        assert longest <= length, (col, longest, length)


def test_not_participating_inserts(conn):   # 17 chars — proves String(20)
    ins(conn, observation_kind="promotion", participation_status="not_participating",
        promotion_type="unknown", promotion_id=None, promotion_key="__none__")
    assert _count(conn) == 1


def test_provider_explicit_inserts_all_three(conn):   # 17 chars — proves String(24)
    ins(conn, source="api", external_product_id="EXTapi",
        seller_revenue_status="provider_explicit", expected_seller_revenue=Decimal("800.00"),
        commission_base_status="provider_explicit", commission_base=Decimal("900.00"),
        subsidy_status="provider_explicit", marketplace_subsidy=Decimal("100.00"))
    assert _count(conn) == 1


# ── CURRENCY (not an enum — a format CHECK) ─────────────────────────────────────
def test_currency_rub_allowed(conn):
    ins(conn, currency="RUB")
    assert _count(conn) == 1


@pytest.mark.parametrize("bad", ["rub", "RU", "RUBL"])
def test_currency_bad_format_blocked(conn, bad):
    with pytest.raises(IntegrityError):
        ins(conn, currency=bad)


def test_currency_null_unknown_allowed(conn):
    ins(conn, currency=None, currency_status="unknown")
    assert _count(conn) == 1


def test_currency_proven_requires_value(conn):
    with pytest.raises(IntegrityError):
        ins(conn, source="api", currency=None, currency_status="proven")


def test_no_default_currency():
    assert T.c.currency.server_default is None and T.c.currency.default is None


# ── OBSERVATION / PARTICIPATION MATRIX ──────────────────────────────────────────
def test_catalog_with_promotion_id_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, observation_kind="catalog", promotion_id="PR1", promotion_key="PR1")


def test_catalog_with_participation_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, observation_kind="catalog", participation_status="active")


def test_promotion_without_participation_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, observation_kind="promotion", participation_status=None,
            promotion_type="unknown", promotion_id="PR1", promotion_key="PR1")


def test_promotion_without_promotion_type_blocked(conn):
    with pytest.raises(IntegrityError):     # promotion observation now REQUIRES promotion_type
        ins(conn, observation_kind="promotion", participation_status="active",
            promotion_type=None, promotion_id="PR1", promotion_key="PR1")


def test_active_without_promotion_id_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, observation_kind="promotion", participation_status="active",
            promotion_type="unknown", promotion_id=None, promotion_key="__none__")


def test_promotion_key_mismatch_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, observation_kind="promotion", participation_status="active",
            promotion_type="unknown", promotion_id="PR1", promotion_key="WRONG")


def test_catalog_is_not_not_participating(conn):
    # a catalog observation carries NO participation — it can never claim not_participating.
    ins(conn)                                   # valid catalog: participation_status NULL
    row = conn.execute(sa.select(T.c.participation_status)).first()
    assert row[0] is None


def test_participation_unknown_allowed(conn):
    ins(conn, observation_kind="promotion", participation_status="unknown",
        promotion_type="unknown", promotion_id=None, promotion_key="__none__")
    assert _count(conn) == 1


# ── PROOF STATUSES ──────────────────────────────────────────────────────────────
def test_csv_provider_explicit_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, source="csv", seller_revenue_status="provider_explicit",
            expected_seller_revenue=Decimal("800.00"))


def test_csv_currency_proven_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, source="csv", currency="RUB", currency_status="proven")


def test_seller_revenue_without_provider_explicit_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, source="api", seller_revenue_status="unknown",
            expected_seller_revenue=Decimal("800.00"))


def test_provider_explicit_seller_revenue_without_value_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, source="api", seller_revenue_status="provider_explicit",
            expected_seller_revenue=None)


def test_commission_base_matrix(conn):
    with pytest.raises(IntegrityError):     # status without value
        ins(conn, source="api", commission_base_status="provider_explicit", commission_base=None)
    with pytest.raises(IntegrityError):     # value without status
        ins(conn, source="api", commission_base_status="unknown", commission_base=Decimal("900.00"))


def test_subsidy_matrix(conn):
    # provider_explicit ⇔ value (biconditional). 0 is a PROVEN value.
    ins(conn, source="api", external_product_id="E0",
        marketplace_subsidy=Decimal("0.00"), subsidy_status="provider_explicit")   # provider_explicit + 0 OK
    ins(conn, source="api", external_product_id="Ena",
        marketplace_subsidy=None, subsidy_status="not_applicable")                 # not_applicable + NULL OK
    ins(conn, source="api", external_product_id="Eun",
        marketplace_subsidy=None, subsidy_status="unknown")                        # unknown + NULL OK
    assert _count(conn) == 3
    with pytest.raises(IntegrityError):     # provider_explicit + NULL blocked
        ins(conn, source="api", external_product_id="Ex1",
            marketplace_subsidy=None, subsidy_status="provider_explicit")
    with pytest.raises(IntegrityError):     # value + unknown blocked
        ins(conn, source="api", external_product_id="Ex2",
            marketplace_subsidy=Decimal("100.00"), subsidy_status="unknown")
    with pytest.raises(IntegrityError):     # value + not_applicable blocked
        ins(conn, source="api", external_product_id="Ex3",
            marketplace_subsidy=Decimal("100.00"), subsidy_status="not_applicable")


def test_null_money_is_not_zero(conn):
    ins(conn, catalog_price=None)
    assert conn.execute(sa.select(T.c.catalog_price)).scalar() is None


def test_negative_money_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, catalog_price=Decimal("-1.00"))


def test_validity_window_blocked(conn):
    with pytest.raises(IntegrityError):
        ins(conn, provider_valid_from=datetime(2026, 7, 10), provider_valid_to=datetime(2026, 7, 1))


# ── IDEMPOTENCY / HISTORY (append-only) ─────────────────────────────────────────
def test_retry_same_run_duplicate_blocked(conn):
    ins(conn, ingest_run_id="runX", external_product_id="E", source="api")
    with pytest.raises(IntegrityError):    # same (store, ext, kind, promo_key, source, run)
        ins(conn, ingest_run_id="runX", external_product_id="E", source="api")


def test_catalog_and_promotion_coexist_in_one_run(conn):
    # one Store/Product/source/run: a catalog observation AND a promotion observation whose
    # promotion_id is NULL (both promotion_key='__none__') must BOTH persist — observation_kind is
    # part of the run key. Repeats of either within the run are still blocked.
    ins(conn, ingest_run_id="R", external_product_id="E", source="api", observation_kind="catalog")
    ins(conn, ingest_run_id="R", external_product_id="E", source="api", observation_kind="promotion",
        participation_status="not_participating", promotion_type="unknown",
        promotion_id=None, promotion_key="__none__")
    assert _count(conn) == 2
    with pytest.raises(IntegrityError):    # repeat catalog same run
        ins(conn, ingest_run_id="R", external_product_id="E", source="api", observation_kind="catalog")
    with pytest.raises(IntegrityError):    # repeat promotion same run
        ins(conn, ingest_run_id="R", external_product_id="E", source="api", observation_kind="promotion",
            participation_status="not_participating", promotion_type="unknown",
            promotion_id=None, promotion_key="__none__")


def test_missing_fields_db_default_and_isolation(conn):
    # raw SQL omitting missing_fields → server_default '[]' → typed read returns []
    conn.execute(text(
        "INSERT INTO marketplace_price_observations"
        "(id, ingest_run_id, marketplace_account_id, marketplace_store_id, product_id, "
        " external_product_id, resolution_status, observation_kind, promotion_key, "
        " catalog_price, currency_status, seller_revenue_status, commission_base_status, "
        " subsidy_status, source, fetched_at, last_verified_at, created_at) "
        "VALUES('raw1','r','accW','s1','p1','E','resolved','catalog','__none__',"
        " 1000, 'unknown','unknown','unknown','unknown','csv', CURRENT_TIMESTAMP, "
        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    got = conn.execute(sa.select(T.c.missing_fields).where(T.c.id == "raw1")).scalar()
    assert got == []
    # explicit value preserved unchanged
    ins(conn, id="exp1", ingest_run_id="r2", external_product_id="E2", missing_fields=["storage"])
    assert conn.execute(sa.select(T.c.missing_fields).where(T.c.id == "exp1")).scalar() == ["storage"]
    # Python default is a CALLABLE (fresh list per row), never a shared mutable [] literal
    d = T.c.missing_fields.default.arg
    assert callable(d) and not isinstance(d, list)


def test_same_identity_new_run_new_row(conn):
    ins(conn, ingest_run_id="r1", external_product_id="E", source="api", catalog_price=Decimal("1000"))
    ins(conn, ingest_run_id="r2", external_product_id="E", source="api", catalog_price=Decimal("1000"))
    assert _count(conn) == 2                # same price, new run → new freshness point


def test_same_identity_new_price_new_run(conn):
    ins(conn, ingest_run_id="r1", external_product_id="E", source="api", catalog_price=Decimal("1000"))
    ins(conn, ingest_run_id="r2", external_product_id="E", source="api", catalog_price=Decimal("700"))
    assert _count(conn) == 2


def test_external_row_id_does_not_block_new_price(conn):
    ins(conn, ingest_run_id="r1", external_product_id="E", external_row_id="STABLE", catalog_price=Decimal("1000"))
    ins(conn, ingest_run_id="r2", external_product_id="E", external_row_id="STABLE", catalog_price=Decimal("700"))
    assert _count(conn) == 2                # stable provider row id is provenance, not a bar


def test_active_and_ended_are_separate_immutable_rows(conn):
    ins(conn, ingest_run_id="r1", external_product_id="E", observation_kind="promotion",
        participation_status="active", promotion_type="ozon_action", promotion_id="PR1", promotion_key="PR1")
    ins(conn, ingest_run_id="r2", external_product_id="E", observation_kind="promotion",
        participation_status="ended", promotion_type="ozon_action", promotion_id="PR1", promotion_key="PR1")
    assert _count(conn) == 2
    # the old active row is untouched (no update path in this slice)
    active = conn.execute(sa.select(sa.func.count()).select_from(T)
                          .where(T.c.participation_status == "active")).scalar()
    assert active == 1


# ── MIGRATION / SCHEMA ──────────────────────────────────────────────────────────
def test_single_alembic_head():
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["rop1a2b3c4d01"], heads


def test_old_tables_preserved(conn):
    names = set(Base.metadata.tables)
    for old in ("imported_product_rows", "protection_evaluations", "product_placements"):
        assert old in names


# ── SECURITY / RETENTION PRE-ENABLE GATE ────────────────────────────────────────
def test_model_has_no_raw_payload_or_secret_columns():
    cols = {c.name.lower() for c in T.columns}
    for bad in ("raw", "payload", "token", "password", "secret", "email", "credential"):
        assert not any(bad in c for c in cols), bad


def test_observation_writer_is_flag_gated_and_unscheduled():
    """Retention pre-enable gate (post PULT-LAUNCH-2.5D). A writer now EXISTS — the Ozon price/promotion
    observation ingest — but the gate is preserved another way: the writer lives in exactly ONE ingest
    module, is reached only through run_api_sync_once (which makes ZERO calls while
    api_data_sync_enabled is False, the default), and NO scheduler tick invokes it. A periodic 15-min
    collector must not appear until retention/compaction ships."""
    import inspect

    here = os.path.dirname(__file__)
    backend = os.path.dirname(here)
    refs = []
    for path in glob.glob(os.path.join(backend, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, backend).replace("\\", "/")
        if rel.startswith(("models/", "tests/")) or "alembic/versions/" in rel:
            continue
        src = open(path, encoding="utf-8").read()
        if "MarketplacePriceObservation" in src or "marketplace_price_observations" in src:
            refs.append(rel)
    # The ONLY production references are the shared change-only WRITER (2.5E-1), the retention SWEEP
    # (2.5E-2B-2, feature OFF, unscheduled), and the read-only advisory RESOLVER (2.5F-B, feature OFF) —
    # the sanctioned modules that touch the table; the resolver only SELECTs (asserted below).
    assert sorted(refs) == ["services/marketplace/ingest/change_only.py",
                            "services/marketplace/retention/observation_sweep.py",
                            "services/protection/observation_resolver.py"], refs
    # the resolver is READ-ONLY: it never writes the observation tables (2.5F-B advisory bridge).
    import services.protection.observation_resolver as _orv
    _resolver_src = inspect.getsource(_orv).lower()
    for _verb in ("insert(", "update(", "delete(", ".add(", ".commit(", ".flush("):
        assert _verb not in _resolver_src, f"observation_resolver must not call {_verb}"

    from config import settings
    assert settings.api_data_sync_enabled is False        # master switch OFF by default

    import tasks.scheduler as scheduler
    assert "run_api_sync_once" not in inspect.getsource(scheduler)   # nothing schedules the ingest
