import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index
from database import Base


class ConnectionVerificationAttempt(Base):
    """One credential-verification attempt. APPEND-ONLY (F1.2b-a).

    The record of what we asked, what came back, and how we classified it — so that
    "is this cabinet connected?" can be answered with evidence instead of a guess. Rows
    are never updated or deleted: the service exposes no mutation for them, because a
    verification history that can be rewritten is not a history. Current state lives on
    `ApiCredential.verification_status`; this table is the trail behind it.

    `credential_id` is NULLABLE on purpose: an attempt can fail BEFORE a credential is
    ever selected — no stored secret for that scope, or a decryption failure — and those
    attempts are exactly the ones worth keeping.

    What this table must NEVER hold, and why:

      * the token, in any form — plaintext or ciphertext. An audit log is not a vault.
      * the marketplace response body, or the text of an error. WB returns RFC-7807
        problem+json (`detail`, `requestId`) and Ozon returns a `message`, both of which
        can carry seller data; `base_client` already puts up to 300 characters of a 4xx
        body into an exception string, and that must not flow in here.

    Only classified, non-identifying facts are stored: an outcome, an HTTP status, a
    retry hint, and whether the response had the shape we expected. `probe_key` and
    `adapter_version` pin the result to the exact probe that produced it, so a later
    change of probe or adapter cannot silently reinterpret old evidence.
    """

    __tablename__ = "connection_verification_attempts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # No ON DELETE anywhere: connections are soft-revoked, never hard-deleted, so the
    # referenced rows do not disappear — and an audit trail must outlive them regardless.
    connection_id = Column(String(36), ForeignKey("marketplace_connections.id"),
                           nullable=False)
    credential_id = Column(String(36), ForeignKey("api_credentials.id"), nullable=True)
    marketplace_account_id = Column(String(36), ForeignKey("marketplace_accounts.id"),
                                    nullable=True)

    marketplace = Column(String(20), nullable=False)
    scope       = Column(String(40), nullable=True)    # NULL for connection-level probes

    probe_key       = Column(String(64), nullable=False)   # which probe ran
    adapter_version = Column(String(20), nullable=False)   # which adapter produced it

    outcome        = Column(String(32), nullable=False)    # VerificationOutcome
    error_category = Column(String(32), nullable=True)     # ErrorCategory

    http_status            = Column(Integer, nullable=True)
    retry_after_seconds    = Column(Integer, nullable=True)
    response_schema_status = Column(String(20), nullable=True)  # ok|missing_fields|unparseable

    started_at  = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_cva_connection", "connection_id", "created_at"),
        Index("ix_cva_credential", "credential_id", "created_at"),
    )
