import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Index, UniqueConstraint,
    ForeignKeyConstraint, CheckConstraint,
)
from database import Base


class MarketplaceStore(Base):
    """Магазин/размещение внутри кабинета (PULT-LAUNCH-1.1/1.2, level 2 of
    Кабинет→Магазин→Товар→Размещение).

    Belongs to a MarketplaceAccount (the cabinet). One row per storefront:
      * wildberries / ozon — exactly ONE store per cabinet (store_key='primary');
      * yandex — one or MANY stores, each a campaign placement. A keyless store
        (created before API) has external_store_id NULL and a stable LOCAL uuid
        store_key; external_store_id (campaignId) is filled later WITHOUT changing
        store_key, so internal references never move.

    DB-enforced invariants (never app-only — an application bug cannot bypass them):
      * store.marketplace == account.marketplace — composite FK to
        marketplace_accounts(id, marketplace); a store can never drift to another
        marketplace than its cabinet.
      * one WB/Ozon store per cabinet — the CHECK forces store_key='primary' for
        them, and UNIQUE(account, store_key) then caps them at exactly one.
      * many Yandex stores incl. keyless — the CHECK forces store_key<>'primary'
        (a local uuid), so distinct keys coexist; external_store_id NULL is allowed
        repeatedly.
      * one campaignId per cabinet — UNIQUE(account, external_store_id) WHERE NOT NULL.
      * clean identifiers — CHECKs reject empty / space-bearing store_key and
        external_store_id.

    Never physically deleted in normal operation: a seller "removing" a store is
    status='archived' (soft), which keeps every historical row's store_id. Only a
    cabinet (MarketplaceAccount) deletion cascades stores/products/placements/rows.
    """
    __tablename__ = "marketplace_stores"

    id                     = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_account_id = Column(String(36), nullable=False)
    # identity-layer canonical name only: wildberries | ozon | yandex (never 'wb')
    marketplace            = Column(String(20), nullable=False)
    # 'primary' for wb/ozon; a stable local uuid (<> 'primary') for yandex
    store_key              = Column(String(36), nullable=False)
    # yandex campaignId; NULL for wb/ozon and for a keyless yandex store until API discovery
    external_store_id      = Column(String(128), nullable=True)
    label                  = Column(String(120), nullable=False)
    placement_type         = Column(String(20), nullable=True)   # FBS | FBY | DBS | Express
    source                 = Column(String(10), nullable=False, default="manual", server_default="manual")  # manual | api
    status                 = Column(String(20), nullable=False, default="active",  server_default="active")   # active | archived
    created_at             = Column(DateTime, default=datetime.utcnow)
    updated_at             = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # store.marketplace is pinned to the cabinet's marketplace
        ForeignKeyConstraint(
            ["marketplace_account_id", "marketplace"],
            ["marketplace_accounts.id", "marketplace_accounts.marketplace"],
            ondelete="CASCADE", name="fk_store_account_mp",
        ),
        UniqueConstraint("marketplace_account_id", "store_key", name="uq_store_account_key"),
        # composite-FK target for product_placements(marketplace_store_id, marketplace_account_id)
        UniqueConstraint("id", "marketplace_account_id", name="uq_store_id_account"),
        Index("uq_store_account_external", "marketplace_account_id", "external_store_id",
              unique=True,
              sqlite_where=external_store_id.isnot(None),
              postgresql_where=external_store_id.isnot(None)),
        CheckConstraint(
            "(marketplace IN ('wildberries','ozon') AND store_key = 'primary') "
            "OR (marketplace = 'yandex' AND store_key <> 'primary')",
            name="ck_store_key_matches_marketplace",
        ),
        CheckConstraint(
            "store_key <> '' AND store_key = trim(store_key) AND store_key NOT LIKE '% %'",
            name="ck_store_key_clean",
        ),
        CheckConstraint(
            "external_store_id IS NULL OR "
            "(external_store_id <> '' AND external_store_id = trim(external_store_id) "
            "AND external_store_id NOT LIKE '% %')",
            name="ck_external_store_id_clean",
        ),
        Index("ix_store_account", "marketplace_account_id"),
    )
