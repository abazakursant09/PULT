"""SECURITY-2C-3B — password-reset token digest at rest.

The raw reset token (secrets.token_urlsafe(32) = 256 bits) travels ONLY inside the emailed link. The
database stores only its SHA-256 digest, so a DB read (backup, replica, an SQLi elsewhere) yields
nothing usable: the digest cannot be turned back into a working reset link.

A plain fast hash is the right choice here — the token already has full cryptographic entropy, so there
is nothing to brute-force and no salt/pepper is needed (unlike a password). The digest is deliberately
NOT keyed with SECRET_KEY: reset links must keep working across a key rotation, and a fast unkeyed hash
of a 256-bit random value is already irreversible.
"""
import hashlib


def hash_reset_token(raw: str) -> str:
    """Return the lowercase hex SHA-256 digest of a raw reset token (64 chars). Pure and
    deterministic; never mutates the input and never logs the value."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()
