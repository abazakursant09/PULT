import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, UniqueConstraint
from database import Base


class MarketplaceAccount(Base):
    """The stable identity of a real external marketplace seller cabinet (F1.1).

    Identity, not credentials. A cabinet exists before any token is issued and
    survives every rotation of it: `MarketplaceConnection` + `ApiCredential` hold
    the current technical credential binding and may be replaced at any time, while
    this row keeps the same `id` for the lifetime of the cabinet. Evidence will be
    keyed on that id, so it must not move when a token does.

    `workspace_id` is the CURRENT owning workspace — not part of the identity key.
    That distinction is what makes an explicit future account transfer a single
    column update instead of a rewrite of ownership history.

    Ownership invariant (launch): one verified external cabinet belongs to at most
    ONE workspace, globally. `UNIQUE(marketplace, external_account_id)` enforces it
    in the database — deliberately WITHOUT `workspace_id` in the key, since a key
    containing it would permit the very thing the invariant forbids: the same
    cabinet claimed by two workspaces at once. Shared access between people is a
    future workspace-membership concern, never a second account row.

    `external_account_id IS NULL` means the cabinet has not been verified yet: F1.1
    connects cabinets but does not discover them. NULL rows intentionally coexist,
    because both SQLite and PostgreSQL treat NULLs as DISTINCT in an ordinary UNIQUE
    constraint — an unverified row is simply outside the uniqueness space. Runtime
    discovery (a later slice) fills the real id, and that very UPDATE is the atomic
    act of claiming ownership: a second workspace resolving to an id already held
    fails on this same constraint, with no read of the owning row and therefore no
    way to disclose who owns it.

    `external_account_id` is only ever a value returned by the marketplace itself.
    It must NEVER be inferred from `user_id`, `workspace_id`, `connection_id`, a
    token, an encrypted token, a token hash, `ozon_client_id`, or the marketplace
    name. A fabricated id would claim global ownership of a cabinet nobody verified.

    Distinct from Reference Data (`marketplace_category_rows` and friends), which
    stays global, marketplace-owned and ownerless. An external seller cabinet has
    exactly one owner; reference data has none.
    """

    __tablename__ = "marketplace_accounts"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # No ON DELETE: account deletion is a SOFT delete (routers/referrals.py sets
    # users.deleted_at), so neither the user nor its workspace ever disappears.
    workspace_id        = Column(String(36), ForeignKey("workspaces.id"), nullable=False)
    marketplace         = Column(String(20), nullable=False)   # wildberries | ozon
    external_account_id = Column(String(128), nullable=True)   # NULL until discovery verifies it
    identity_status     = Column(String(32), nullable=False)   # unverified_legacy | unverified | verified
    label               = Column(String(120), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("marketplace", "external_account_id", name="uq_mp_account_mp_ext"),
        Index("ix_mp_account_ws", "workspace_id"),
    )
