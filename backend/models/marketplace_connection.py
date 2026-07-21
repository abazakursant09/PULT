import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index, Integer, UniqueConstraint
from database import Base


class MarketplaceConnection(Base):
    """The current credential binding to one seller cabinet. Tokens live in ApiCredential.

    Not the cabinet's identity: that is `MarketplaceAccount`, which this row points at
    and which outlives any rotation or reconnect here (F1.1).

    Two INDEPENDENT axes describe this row (F1.2a). Collapsing them is a mistake:

      * `status` — the connection LIFECYCLE and the execution gate. `connected` means
        the seller has connected this cabinet and has not revoked it; it is what the
        executor, the measurement bridge and the WB review sync require before they
        will touch a marketplace. It says NOTHING about whether the credentials work.

      * `verification_status` — whether a real marketplace verification flow ever
        positively confirmed these credentials. Storing ciphertext is not verifying it:
        `POST /connections` encrypts whatever string it is given and calls no
        marketplace at all. Until a verifier exists, the honest answer is `unverified`,
        and this column is the only place allowed to say otherwise.

    So `status = "connected"` + `verification_status = "unverified"` is the normal,
    truthful state today: usable under the existing execution contract, never yet
    checked against the marketplace. Do not read `status` as proof of verification.
    """

    __tablename__ = "marketplace_connections"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String(36), nullable=False)
    marketplace    = Column(String(20), nullable=False)            # wildberries | ozon
    label          = Column(String(120), nullable=True)
    status         = Column(String(20), nullable=False, default="connected")  # connected|invalid|revoked
    scopes         = Column(JSON, nullable=False, default=list)    # ["feedbacks","prices",...]
    ozon_client_id = Column(String(64), nullable=True)             # Ozon needs Client-Id alongside key
    last_check_at  = Column(DateTime, nullable=True)
    # AR-AUTO-FILL review-sync cadence (per connection). `review_sync_next_at` is the earliest the
    # scheduler may auto-sync this connection again — a fixed interval after a success, an
    # exponentially longer one after each consecutive failure (capped). `review_sync_fail_count`
    # drives that backoff and resets to 0 on success. `review_sync_cursor` is a persistent keyset
    # cursor over the connection's products by their stable Product.id: each sync processes the next
    # batch (Product.id > cursor), advances the cursor, and wraps to the start (NULL) after the last
    # product — so a large store is covered over several cycles, no product starves, and a restart
    # resumes where it left off (not an OFFSET, which add/delete could skip). NULL = start of a round.
    review_sync_next_at    = Column(DateTime, nullable=True)
    review_sync_fail_count = Column(Integer, nullable=False, default=0, server_default="0")
    review_sync_cursor     = Column(String(36), nullable=True)
    # Verification axis (F1.2a). `unverified` is the ONLY value this slice can write:
    # no marketplace probe exists yet. A later verifier owns `verified` and the failure
    # values. Any credential write on this connection resets both columns, because the
    # new secret has not been checked either.
    verification_status = Column(String(32), nullable=False, default="unverified",
                                 server_default="unverified")
    verified_at         = Column(DateTime, nullable=True)   # stays NULL until a real verifier succeeds
    # Identity links (F1.1). NULLABLE: rows predating F1.1 whose user has no workspace
    # cannot be backfilled (user_id carries no FK, so it may not resolve at all), and a
    # NOT NULL column would make the migration destructive for them. The API path always
    # populates both; tightening to NOT NULL waits until every writer is proven to.
    workspace_id           = Column(String(36), ForeignKey("workspaces.id"), nullable=True)
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_mp_conn_user", "user_id"),
        Index("ix_mp_conn_user_mp", "user_id", "marketplace"),
        Index("ix_mp_conn_account", "marketplace_account_id"),
        # PULT-LAUNCH-1.3: one API connection per cabinet. marketplace_account_id stays nullable
        # (a CSV-only cabinet has zero connections; a NOT NULL promotion is a later preflight-gated
        # step), and NULLs are distinct in both PostgreSQL and SQLite, so keyless rows never collide.
        UniqueConstraint("marketplace_account_id", name="uq_mp_conn_account"),
    )
