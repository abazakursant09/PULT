"""PULT-LAUNCH-1.3 — store/product schema (Кабинет→Магазин→Товар→Размещение)

Adds the two-level store model on top of the cabinet:
  * marketplace_stores      — one store per WB/Ozon cabinet (store_key='primary'),
                              one-or-many per Yandex cabinet (local-uuid store_key).
  * product_placements      — explicit Product↔Store membership, cross-cabinet
                              impossible via two composite FKs.
Extends products (cabinet-level card: marketplace_account_id, external_product_id),
the three operational Imported* tables (marketplace_account_id CASCADE +
marketplace_store_id SET NULL + source + fetched_at) and imported_card_content
(account only — card content is cabinet-level, no store_id). Adds the composite-FK
target uniques and one-connection-per-cabinet UNIQUE.

Backfill is CONSERVATIVE and never invents a cabinet: it creates exactly one
internal store per EXISTING MarketplaceAccount and links a user's Products / Imported
rows to that account only when (user, normalized-marketplace) resolves to exactly one
account. CSV-only users without an account, unknown marketplace values, and any
ambiguity are left NULL (needs_review / unassigned) — no guessed store. NOT NULL is
deliberately NOT added here (that is a later, preflight-gated step).

Revision ID: spl1a2b3c4d01
Revises: arf1a2b3c4d01
Create Date: 2026-07-21
"""
from typing import Sequence, Union
import uuid as _uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "spl1a2b3c4d01"
down_revision: Union[str, None] = "arf1a2b3c4d01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# identity-layer aliases (mirror services.marketplace.identity_normalize; kept local so the
# migration is self-contained and never depends on app code that may change later).
_ID_ALIASES = {
    "wb": "wildberries", "wildberries": "wildberries",
    "ozon": "ozon",
    "ym": "yandex", "yandex": "yandex", "yandex_market": "yandex",
}


def _norm(mp) -> "str | None":
    if mp is None:
        return None
    return _ID_ALIASES.get(str(mp).strip().lower())


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1) composite-FK target on marketplace_accounts(id, marketplace) ──────────
    with op.batch_alter_table("marketplace_accounts") as b:
        b.create_unique_constraint("uq_mp_account_id_mp", ["id", "marketplace"])

    # ── 2) products: cabinet-level card columns + FK + composite-FK target ───────
    with op.batch_alter_table("products") as b:
        b.add_column(sa.Column("marketplace_account_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("external_product_id", sa.String(length=255), nullable=True))
        b.create_foreign_key("fk_product_account", "marketplace_accounts",
                             ["marketplace_account_id"], ["id"], ondelete="CASCADE")
        b.create_unique_constraint("uq_product_id_account", ["id", "marketplace_account_id"])
    op.create_index("uq_product_account_external", "products",
                    ["marketplace_account_id", "external_product_id"], unique=True,
                    sqlite_where=sa.text("external_product_id IS NOT NULL"),
                    postgresql_where=sa.text("external_product_id IS NOT NULL"))
    op.create_index("ix_product_account", "products", ["marketplace_account_id"])
    op.create_index("ix_product_account_sku", "products", ["marketplace_account_id", "sku"])

    # ── 3) marketplace_stores ────────────────────────────────────────────────────
    op.create_table(
        "marketplace_stores",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("marketplace_account_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace", sa.String(length=20), nullable=False),
        sa.Column("store_key", sa.String(length=36), nullable=False),
        sa.Column("external_store_id", sa.String(length=128), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("placement_type", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["marketplace_account_id", "marketplace"],
            ["marketplace_accounts.id", "marketplace_accounts.marketplace"],
            ondelete="CASCADE", name="fk_store_account_mp"),
        sa.UniqueConstraint("marketplace_account_id", "store_key", name="uq_store_account_key"),
        sa.UniqueConstraint("id", "marketplace_account_id", name="uq_store_id_account"),
        sa.CheckConstraint(
            "(marketplace IN ('wildberries','ozon') AND store_key = 'primary') "
            "OR (marketplace = 'yandex' AND store_key <> 'primary')",
            name="ck_store_key_matches_marketplace"),
        sa.CheckConstraint(
            "store_key <> '' AND store_key = trim(store_key) AND store_key NOT LIKE '% %'",
            name="ck_store_key_clean"),
        sa.CheckConstraint(
            "external_store_id IS NULL OR (external_store_id <> '' "
            "AND external_store_id = trim(external_store_id) AND external_store_id NOT LIKE '% %')",
            name="ck_external_store_id_clean"),
    )
    op.create_index("uq_store_account_external", "marketplace_stores",
                    ["marketplace_account_id", "external_store_id"], unique=True,
                    sqlite_where=sa.text("external_store_id IS NOT NULL"),
                    postgresql_where=sa.text("external_store_id IS NOT NULL"))
    op.create_index("ix_store_account", "marketplace_stores", ["marketplace_account_id"])

    # ── 4) product_placements ────────────────────────────────────────────────────
    op.create_table(
        "product_placements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_store_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_account_id", sa.String(length=36), nullable=False),
        sa.Column("external_offer_id", sa.String(length=255), nullable=True),
        sa.Column("seller_sku", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id", "marketplace_account_id"],
            ["products.id", "products.marketplace_account_id"],
            ondelete="CASCADE", name="fk_placement_product"),
        sa.ForeignKeyConstraint(
            ["marketplace_store_id", "marketplace_account_id"],
            ["marketplace_stores.id", "marketplace_stores.marketplace_account_id"],
            ondelete="CASCADE", name="fk_placement_store"),
        sa.UniqueConstraint("marketplace_store_id", "product_id", name="uq_placement_store_product"),
        sa.CheckConstraint(
            "external_offer_id IS NULL OR (external_offer_id <> '' "
            "AND external_offer_id = trim(external_offer_id) AND external_offer_id NOT LIKE '% %')",
            name="ck_placement_offer_clean"),
    )
    op.create_index("uq_placement_store_offer", "product_placements",
                    ["marketplace_store_id", "external_offer_id"], unique=True,
                    sqlite_where=sa.text("external_offer_id IS NOT NULL"),
                    postgresql_where=sa.text("external_offer_id IS NOT NULL"))
    op.create_index("ix_placement_product", "product_placements", ["product_id"])
    op.create_index("ix_placement_store", "product_placements", ["marketplace_store_id"])

    # ── 5) operational Imported* tables: account (CASCADE) + store (SET NULL) ─────
    for tbl in ("imported_product_rows", "imported_finance_rows", "imported_return_rows"):
        with op.batch_alter_table(tbl) as b:
            b.add_column(sa.Column("marketplace_account_id", sa.String(length=36), nullable=True))
            b.add_column(sa.Column("marketplace_store_id", sa.String(length=36), nullable=True))
            b.add_column(sa.Column("source", sa.String(length=10), nullable=False, server_default="csv"))
            b.add_column(sa.Column("fetched_at", sa.DateTime(), nullable=True))
            b.create_foreign_key(f"fk_{tbl}_account", "marketplace_accounts",
                                 ["marketplace_account_id"], ["id"], ondelete="CASCADE")
            b.create_foreign_key(f"fk_{tbl}_store", "marketplace_stores",
                                 ["marketplace_store_id"], ["id"], ondelete="SET NULL")

    # card content is cabinet-level: account only, NO store_id
    with op.batch_alter_table("imported_card_content_rows") as b:
        b.add_column(sa.Column("marketplace_account_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("source", sa.String(length=10), nullable=False, server_default="csv"))
        b.add_column(sa.Column("fetched_at", sa.DateTime(), nullable=True))
        b.create_foreign_key("fk_imported_card_content_rows_account", "marketplace_accounts",
                             ["marketplace_account_id"], ["id"], ondelete="CASCADE")

    # ── 6) one API connection per cabinet ────────────────────────────────────────
    with op.batch_alter_table("marketplace_connections") as b:
        b.create_unique_constraint("uq_mp_conn_account", ["marketplace_account_id"])

    # ── 7) conservative backfill (no invented store; unknown/ambiguous stay NULL) ─
    _backfill(bind)


def _backfill(bind) -> None:
    now = "CURRENT_TIMESTAMP"

    # 7a) one internal store per EXISTING account; remember account_id -> store_id
    account_store: dict = {}
    accounts = bind.execute(text(
        "SELECT id, marketplace FROM marketplace_accounts")).fetchall()
    for acc_id, mp in accounts:
        norm = _norm(mp)
        if norm is None:
            continue  # unknown marketplace on account -> no store (needs_review)
        store_key = "primary" if norm in ("wildberries", "ozon") else _uuid.uuid4().hex
        store_id = _uuid.uuid4().hex
        bind.execute(text(
            "INSERT INTO marketplace_stores"
            "(id, marketplace_account_id, marketplace, store_key, label, source, status, created_at, updated_at) "
            f"VALUES (:id, :acc, :mp, :key, :label, 'manual', 'active', {now}, {now})"),
            {"id": store_id, "acc": acc_id, "mp": norm, "key": store_key, "label": "Магазин"})
        account_store[acc_id] = store_id

    # 7b) resolve (owner_user_id, canonical_marketplace) -> account_id, keeping only
    #     the UNAMBIGUOUS ones (exactly one account for the pair).
    rows = bind.execute(text(
        "SELECT a.id, a.marketplace, w.owner_user_id "
        "FROM marketplace_accounts a JOIN workspaces w ON w.id = a.workspace_id")).fetchall()
    by_pair: dict = {}
    for acc_id, mp, owner in rows:
        norm = _norm(mp)
        if norm is None or owner is None:
            continue
        by_pair.setdefault((owner, norm), []).append(acc_id)
    resolved = {pair: accs[0] for pair, accs in by_pair.items() if len(accs) == 1}

    # 7c) link products to their cabinet + create a placement (unambiguous only)
    products = bind.execute(text(
        "SELECT id, user_id, marketplace FROM products WHERE marketplace_account_id IS NULL")).fetchall()
    for pid, uid, mp in products:
        acc_id = resolved.get((uid, _norm(mp)))
        if acc_id is None:
            continue  # CSV-only / ambiguous / unknown -> stays NULL (unassigned)
        bind.execute(text(
            "UPDATE products SET marketplace_account_id = :acc WHERE id = :pid"),
            {"acc": acc_id, "pid": pid})
        store_id = account_store.get(acc_id)
        if store_id is not None:
            bind.execute(text(
                "INSERT INTO product_placements"
                "(id, product_id, marketplace_store_id, marketplace_account_id, status, source, first_seen_at, last_seen_at) "
                f"VALUES (:id, :pid, :sid, :acc, 'active', 'csv', {now}, {now})"),
                {"id": _uuid.uuid4().hex, "pid": pid, "sid": store_id, "acc": acc_id})

    # 7d) link operational Imported* rows (account + store) when unambiguous
    for tbl in ("imported_product_rows", "imported_finance_rows", "imported_return_rows"):
        irows = bind.execute(text(
            f"SELECT id, user_id, marketplace FROM {tbl} WHERE marketplace_account_id IS NULL")).fetchall()
        for rid, uid, mp in irows:
            acc_id = resolved.get((uid, _norm(mp)))
            if acc_id is None:
                continue
            bind.execute(text(
                f"UPDATE {tbl} SET marketplace_account_id = :acc, marketplace_store_id = :sid WHERE id = :rid"),
                {"acc": acc_id, "sid": account_store.get(acc_id), "rid": rid})

    # 7e) card content rows: account only (cabinet-level)
    crows = bind.execute(text(
        "SELECT id, user_id, marketplace FROM imported_card_content_rows WHERE marketplace_account_id IS NULL")).fetchall()
    for rid, uid, mp in crows:
        acc_id = resolved.get((uid, _norm(mp)))
        if acc_id is None:
            continue
        bind.execute(text(
            "UPDATE imported_card_content_rows SET marketplace_account_id = :acc WHERE id = :rid"),
            {"acc": acc_id, "rid": rid})


def downgrade() -> None:
    with op.batch_alter_table("marketplace_connections") as b:
        b.drop_constraint("uq_mp_conn_account", type_="unique")

    with op.batch_alter_table("imported_card_content_rows") as b:
        b.drop_constraint("fk_imported_card_content_rows_account", type_="foreignkey")
        b.drop_column("fetched_at")
        b.drop_column("source")
        b.drop_column("marketplace_account_id")
    for tbl in ("imported_return_rows", "imported_finance_rows", "imported_product_rows"):
        with op.batch_alter_table(tbl) as b:
            b.drop_constraint(f"fk_{tbl}_store", type_="foreignkey")
            b.drop_constraint(f"fk_{tbl}_account", type_="foreignkey")
            b.drop_column("fetched_at")
            b.drop_column("source")
            b.drop_column("marketplace_store_id")
            b.drop_column("marketplace_account_id")

    op.drop_table("product_placements")
    op.drop_table("marketplace_stores")

    op.drop_index("ix_product_account_sku", table_name="products")
    op.drop_index("ix_product_account", table_name="products")
    op.drop_index("uq_product_account_external", table_name="products")
    with op.batch_alter_table("products") as b:
        b.drop_constraint("uq_product_id_account", type_="unique")
        b.drop_constraint("fk_product_account", type_="foreignkey")
        b.drop_column("external_product_id")
        b.drop_column("marketplace_account_id")

    with op.batch_alter_table("marketplace_accounts") as b:
        b.drop_constraint("uq_mp_account_id_mp", type_="unique")
