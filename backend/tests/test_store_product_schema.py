"""PULT-LAUNCH-1.3 — DB-level guarantees for Кабинет→Магазин→Товар→Размещение.

Every invariant that the design says must be enforced by the DATABASE (not the app)
is proven here against SQLite with foreign keys ON — the same enforcement PostgreSQL
gives in production. Plus the identity normalizer and a migration up/down/re-upgrade.
"""
import os
import pathlib
import sqlite3
import tempfile
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

import models  # noqa: F401 — register full metadata
from database import Base
from services.marketplace.identity_normalize import normalize_identity_marketplace


def _fk_engine():
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _fk(dbapi_con, _rec):          # SQLite enforces FKs only when asked
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
    yield c
    c.close()


def _store(c, sid, acc, mp, key):
    c.execute(text(
        "INSERT INTO marketplace_stores"
        "(id,marketplace_account_id,marketplace,store_key,label,source,status,created_at,updated_at) "
        "VALUES(:id,:a,:m,:k,'S','manual','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": sid, "a": acc, "m": mp, "k": key})


def _product(c, pid, acc, mp, sku="SKU", ext=None):
    c.execute(text(
        "INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id,external_product_id) "
        "VALUES(:id,'u1','N',:mp,:sku,:acc,:ext)"),
        {"id": pid, "mp": mp, "sku": sku, "acc": acc, "ext": ext})


def _placement(c, ppid, pid, sid, acc):
    c.execute(text(
        "INSERT INTO product_placements"
        "(id,product_id,marketplace_store_id,marketplace_account_id,status,source,first_seen_at,last_seen_at) "
        "VALUES(:id,:p,:s,:a,'active','csv',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": ppid, "p": pid, "s": sid, "a": acc})


# ── store multiplicity ───────────────────────────────────────────────────────
def test_second_wb_store_blocked(conn):
    _store(conn, "s1", "accW", "wildberries", "primary")
    with pytest.raises(IntegrityError):
        _store(conn, "s2", "accW", "wildberries", "primary")


def test_second_ozon_store_blocked(conn):
    _store(conn, "o1", "accO", "ozon", "primary")
    with pytest.raises(IntegrityError):
        _store(conn, "o2", "accO", "ozon", "primary")


def test_wb_store_key_must_be_primary(conn):
    with pytest.raises(IntegrityError):
        _store(conn, "s1", "accW", "wildberries", uuid.uuid4().hex)


def test_yandex_multiple_keyless_allowed(conn):
    _store(conn, "y1", "accY", "yandex", uuid.uuid4().hex)
    _store(conn, "y2", "accY", "yandex", uuid.uuid4().hex)
    assert conn.execute(text("SELECT count(*) FROM marketplace_stores WHERE marketplace_account_id='accY'")).scalar() == 2


def test_yandex_store_key_primary_blocked(conn):
    with pytest.raises(IntegrityError):
        _store(conn, "y1", "accY", "yandex", "primary")


def test_duplicate_campaign_id_blocked(conn):
    _store(conn, "y1", "accY", "yandex", "k1")
    _store(conn, "y2", "accY", "yandex", "k2")
    conn.execute(text("UPDATE marketplace_stores SET external_store_id='555' WHERE id='y1'"))
    with pytest.raises(IntegrityError):
        conn.execute(text("UPDATE marketplace_stores SET external_store_id='555' WHERE id='y2'"))


def test_store_marketplace_must_match_account(conn):
    # accW is wildberries; a store claiming ozon must be rejected by the composite FK
    with pytest.raises(IntegrityError):
        _store(conn, "bad", "accW", "ozon", "primary")


def test_store_key_padding_rejected(conn):
    with pytest.raises(IntegrityError):
        _store(conn, "s1", "accW", "wildberries", " primary")


# ── placement / cross-cabinet ────────────────────────────────────────────────
def test_cross_cabinet_placement_blocked(conn):
    _store(conn, "sW", "accW", "wildberries", "primary")
    _store(conn, "sY", "accY", "yandex", "ky")
    _product(conn, "pW", "accW", "wildberries")
    # product of cabinet accW cannot be placed in a store of cabinet accY (declared accY)
    with pytest.raises(IntegrityError):
        _placement(conn, "pp", "pW", "sY", "accY")
    # nor by smuggling accW while pointing at accY's store
    with pytest.raises(IntegrityError):
        _placement(conn, "pp", "pW", "sY", "accW")


def test_duplicate_placement_blocked(conn):
    _store(conn, "sW", "accW", "wildberries", "primary")
    _product(conn, "pW", "accW", "wildberries")
    _placement(conn, "pp1", "pW", "sW", "accW")
    with pytest.raises(IntegrityError):
        _placement(conn, "pp2", "pW", "sW", "accW")


def test_product_visible_via_placement_without_metrics(conn):
    # zero sales, zero stock: membership is still known through the placement row
    _store(conn, "sW", "accW", "wildberries", "primary")
    _product(conn, "pW", "accW", "wildberries")
    _placement(conn, "pp1", "pW", "sW", "accW")
    rows = conn.execute(text(
        "SELECT product_id FROM product_placements WHERE marketplace_store_id='sW'")).fetchall()
    assert [r[0] for r in rows] == ["pW"]


def test_sku_conflict_not_silently_merged(conn):
    # same SKU inside one cabinet is NOT unique-constrained: two distinct rows coexist,
    # so the app can flag the conflict instead of the DB silently merging them.
    _product(conn, "p1", "accW", "wildberries", sku="DUP")
    _product(conn, "p2", "accW", "wildberries", sku="DUP")
    assert conn.execute(text("SELECT count(*) FROM products WHERE sku='DUP'")).scalar() == 2


def test_same_external_product_id_blocked_in_cabinet(conn):
    _product(conn, "p1", "accW", "wildberries", ext="NM1")
    with pytest.raises(IntegrityError):
        _product(conn, "p2", "accW", "wildberries", ext="NM1")


# ── deletion policy ──────────────────────────────────────────────────────────
def test_account_delete_purges_commercial_data(conn):
    _store(conn, "sW", "accW", "wildberries", "primary")
    _product(conn, "pW", "accW", "wildberries")
    conn.execute(text(
        "INSERT INTO imported_finance_rows(id,import_id,user_id,marketplace,revenue,commission,logistics,"
        "ad_spend,net_profit,quantity,marketplace_account_id,marketplace_store_id,source) "
        "VALUES('f1','imp','u1','wildberries',100,1,1,1,50,1,'accW','sW','csv')"))
    conn.execute(text("DELETE FROM marketplace_accounts WHERE id='accW'"))
    assert conn.execute(text("SELECT count(*) FROM products WHERE marketplace_account_id='accW'")).scalar() == 0
    assert conn.execute(text("SELECT count(*) FROM imported_finance_rows")).scalar() == 0
    assert conn.execute(text("SELECT count(*) FROM marketplace_stores")).scalar() == 0


def test_store_archive_keeps_history_and_store_id(conn):
    _store(conn, "sW", "accW", "wildberries", "primary")
    conn.execute(text(
        "INSERT INTO imported_finance_rows(id,import_id,user_id,marketplace,revenue,commission,logistics,"
        "ad_spend,net_profit,quantity,marketplace_account_id,marketplace_store_id,source) "
        "VALUES('f1','imp','u1','wildberries',100,1,1,1,50,1,'accW','sW','csv')"))
    # archiving is a soft status change — it must NOT null store_id or drop the row
    conn.execute(text("UPDATE marketplace_stores SET status='archived' WHERE id='sW'"))
    row = conn.execute(text("SELECT marketplace_store_id FROM imported_finance_rows WHERE id='f1'")).scalar()
    assert row == "sW"
    assert conn.execute(text("SELECT status FROM marketplace_stores WHERE id='sW'")).scalar() == "archived"


# ── connection uniqueness ────────────────────────────────────────────────────
def test_one_connection_per_cabinet(conn):
    conn.execute(text("INSERT INTO marketplace_connections(id,user_id,marketplace,status,scopes,marketplace_account_id) "
                      "VALUES('c1','u1','wildberries','connected','[]','accW')"))
    with pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO marketplace_connections(id,user_id,marketplace,status,scopes,marketplace_account_id) "
                          "VALUES('c2','u1','wildberries','connected','[]','accW')"))


def test_keyless_connections_do_not_collide(conn):
    # two CSV-only connections with NULL account are allowed (NULLs distinct)
    conn.execute(text("INSERT INTO marketplace_connections(id,user_id,marketplace,status,scopes) "
                      "VALUES('c1','u1','wildberries','connected','[]')"))
    conn.execute(text("INSERT INTO marketplace_connections(id,user_id,marketplace,status,scopes) "
                      "VALUES('c2','u1','ozon','connected','[]')"))
    assert conn.execute(text("SELECT count(*) FROM marketplace_connections")).scalar() == 2


# ── identity normalizer ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("wb", "wildberries"), ("wildberries", "wildberries"), ("WB", "wildberries"),
    ("ozon", "ozon"), ("ym", "yandex"), ("yandex", "yandex"), ("yandex_market", "yandex"),
    (" Ozon ", "ozon"),
])
def test_identity_normalizer_known(raw, expected):
    assert normalize_identity_marketplace(raw) == expected


@pytest.mark.parametrize("raw", ["megamarket", "aliexpress", "", "wb2", None])
def test_identity_normalizer_unknown_not_guessed(raw):
    assert normalize_identity_marketplace(raw) is None


# ── migration up/down/re-upgrade ─────────────────────────────────────────────
def test_migration_upgrade_downgrade_reupgrade(tmp_path):
    from alembic.config import Config
    from alembic import command

    db = tmp_path / "mig.db"
    os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "arf1a2b3c4d01")
        # seed legacy rows at the pre-migration revision
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x')")
        con.execute("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP)")
        con.execute("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                    "VALUES('acc1','ws1','wildberries','unverified')")
        con.execute("INSERT INTO products(id,user_id,name,marketplace) VALUES('p1','u1','Card','wb')")
        con.execute("INSERT INTO products(id,user_id,name,marketplace) VALUES('p2','u1','Orphan','megamarket')")
        con.commit(); con.close()

        command.upgrade(cfg, "head")
        con = sqlite3.connect(db)
        # one store for the existing account; legacy 'wb' product linked; unknown-mp product left NULL
        assert con.execute("SELECT count(*) FROM marketplace_stores").fetchone()[0] == 1
        assert con.execute("SELECT marketplace_account_id FROM products WHERE id='p1'").fetchone()[0] == "acc1"
        assert con.execute("SELECT marketplace_account_id FROM products WHERE id='p2'").fetchone()[0] is None
        assert con.execute("SELECT count(*) FROM product_placements").fetchone()[0] == 1
        con.close()

        command.downgrade(cfg, "arf1a2b3c4d01")
        con = sqlite3.connect(db)
        cols = [r[1] for r in con.execute("PRAGMA table_info('products')")]
        assert "marketplace_account_id" not in cols          # column removed
        # legacy data survived the round-trip untouched
        assert con.execute("SELECT name FROM products WHERE id='p1'").fetchone()[0] == "Card"
        con.close()

        command.upgrade(cfg, "head")  # re-upgrade must succeed
        con = sqlite3.connect(db)
        assert con.execute("SELECT count(*) FROM marketplace_stores").fetchone()[0] == 1
        con.close()
    finally:
        os.environ.pop("ALEMBIC_DATABASE_URL", None)


def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1
