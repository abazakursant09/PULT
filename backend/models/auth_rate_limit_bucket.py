"""
SECURITY-2C-2 — PostgreSQL-atomic auth throttle state (brute-force / credential-stuffing / spraying).

One row per (action, dimension, key_hash) sliding window. It stores NO plaintext: key_hash is
HMAC-SHA256(SECRET_KEY, "pult:auth-throttle:v1:<dimension>:<normalized-value>") — an email/IP can never
be recovered from it, and there is NO User FK, so an unknown email gets the exact same throttling as a
real one (no existence oracle). Counters advance via a single atomic upsert (services/auth_throttle.py);
this model is state only.

  action     ∈ login | register | email | reset          (email = forgot-password + resend-verification)
  dimension  ∈ pair (identity+IP) | identity | ip
  attempts   — failures in the current window (login) / requests (other actions); success compensates -1.
  blocked_until — set when the limit is hit; NEVER decreased by a race (services enforce GREATEST).
  expires_at — window_started_at + window + block (+grace); the opportunistic sweep deletes only rows
               whose expires_at AND blocked_until are both in the past.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, CheckConstraint, PrimaryKeyConstraint, Index
from database import Base

ACTIONS = ("login", "register", "email", "reset")
DIMENSIONS = ("pair", "identity", "ip")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class AuthRateLimitBucket(Base):
    __tablename__ = "auth_rate_limit_buckets"

    action            = Column(String(16), nullable=False)
    dimension         = Column(String(16), nullable=False)
    key_hash          = Column(String(64), nullable=False)   # HMAC-SHA256 hex — never a plaintext email/IP
    window_started_at = Column(DateTime(timezone=True), nullable=False)
    attempts          = Column(Integer, nullable=False, default=0, server_default="0")
    blocked_until     = Column(DateTime(timezone=True), nullable=True)
    updated_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    expires_at        = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("action", "dimension", "key_hash", name="pk_auth_throttle"),
        CheckConstraint("attempts >= 0", name="ck_auth_throttle_attempts_nonneg"),
        CheckConstraint(_in("action", ACTIONS), name="ck_auth_throttle_action"),
        CheckConstraint(_in("dimension", DIMENSIONS), name="ck_auth_throttle_dimension"),
        # cleanup sweeps by expires_at
        Index("ix_auth_throttle_expires", "expires_at"),
    )
