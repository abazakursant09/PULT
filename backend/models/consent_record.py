import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index, ForeignKey, CheckConstraint
from database import Base


class ConsentRecord(Base):
    """LEGAL-PRELAUNCH-F2 (blocker #6) — server-side, append-only evidence that a user gave
    consent at registration / account recovery.

    One row per consent ACTION. Rows are never updated or deleted by the application: a new
    consent action (a recovery re-registration, a future re-consent) inserts a NEW row, so the
    version the subject actually consented to is preserved even after the document set changes.
    There is deliberately NO API/service update or delete path. This is APP-LEVEL append-only;
    a DB-level UPDATE prohibition (triggers) is intentionally NOT added — it is not portable
    across SQLite/PostgreSQL without complicating migrations, and no writer mutates a row.

    Minimal, non-expanding collection: user id + a SERVER-generated UTC timestamp + the SERVER
    document-set version + a fixed context. It NEVER stores IP, user-agent, fingerprint/device,
    email, document text, tokens, a client timestamp, or a client-supplied version. The existing
    `User.registered_ip` is not duplicated here and is not extended.

    Legal sufficiency of this mechanism under 152-ФЗ is NOT asserted here — it REQUIRES RUSSIAN
    COUNSEL REVIEW. Legacy users (registered before F2) simply have zero rows; consent is NEVER
    backfilled or fabricated for them.
    """
    __tablename__ = "consent_records"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Soft-delete + recovery reuse the same user row (routers/referrals.py, routers/auth.py), so the
    # referenced user never disappears; no ON DELETE behaviour is required (matches Workspace).
    user_id         = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    # SERVER clock only — the client never supplies a timestamp. Naive UTC (datetime.utcnow), matching
    # the project-wide DateTime idiom (User.created_at, Workspace.created_at, automation_rule/protection
    # consent_at); the value is server-authoritative UTC regardless of tz-awareness.
    consent_at      = Column(DateTime, nullable=False)
    # SERVER constant only (settings.consent_doc_version) — the client never supplies a version.
    consent_version = Column(String(16), nullable=False)
    # Closed vocabulary; enforced by a CHECK so an unknown context cannot be written.
    context         = Column(String(32), nullable=False)
    created_at      = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_consent_records_user", "user_id"),
        CheckConstraint("context IN ('registration', 'recovery')", name="ck_consent_context"),
    )
