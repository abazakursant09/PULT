"""
Credential vault (RFC §6). Marketplace API tokens are encrypted at rest with
Fernet (AES-128-CBC + HMAC). The key comes from settings.cred_enc_key.

Tokens are decrypted only at dispatch time, inside the executor, and never
written to logs, payloads, results, or exception text.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import settings

log = logging.getLogger(__name__)


def _resolve_key() -> bytes:
    """
    Return a valid urlsafe-base64 32-byte Fernet key.

    Production MUST set CRED_ENC_KEY (a real `Fernet.generate_key()`). In
    development, if unset, we deterministically derive one from secret_key so
    the layer is usable locally — but we warn loudly because rotating
    secret_key would then orphan stored tokens.
    """
    raw = settings.cred_enc_key.strip()
    if raw:
        # accept a proper Fernet key as-is; validate length/shape
        try:
            Fernet(raw.encode())
            return raw.encode()
        except Exception:
            # treat as arbitrary secret -> derive a Fernet key from it
            digest = hashlib.sha256(raw.encode()).digest()
            return base64.urlsafe_b64encode(digest)

    if settings.app_env == "production":
        raise RuntimeError(
            "CRED_ENC_KEY is required in production (marketplace token encryption)."
        )
    log.warning(
        "⚠️  CRED_ENC_KEY not set — deriving a dev key from SECRET_KEY. "
        "Do NOT use in production; set CRED_ENC_KEY=$(python -c "
        "'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')."
    )
    digest = hashlib.sha256(("me-vault:" + settings.secret_key).encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_resolve_key())
    return _fernet


def encrypt(secret: str) -> bytes:
    """Encrypt a plaintext token for storage in ApiCredential.secret_enc."""
    if not secret:
        raise ValueError("refusing to encrypt empty secret")
    return _cipher().encrypt(secret.encode())


def decrypt(blob: bytes) -> str:
    """Decrypt a stored token. Raises ValueError on tamper/wrong key (no token leak)."""
    try:
        return _cipher().decrypt(blob).decode()
    except InvalidToken as exc:
        raise ValueError("credential decryption failed (bad key or tampered)") from exc


def fingerprint(secret: str) -> str:
    """A keyed, non-reversible fingerprint of a token, ONLY to detect the SAME token being
    reused across two connections (PULT-LAUNCH-1.4.5D, Wildberries).

    HMAC, not a plain hash: a bare SHA-256 of a token is offline-guessable and, worse, would be
    the same value in any system that hashed the same token — so it could correlate a seller's key
    across services. Keying it with the vault secret (the same trust domain that already guards the
    ciphertext, production-mandatory) means the fingerprint is meaningless without that secret and
    cannot be recomputed by anyone who only sees the database.

    This is an equality probe and NOTHING else: it never identifies a cabinet, is never returned by
    the API, is never logged, and must never drive an automatic merge. Two connections sharing a
    fingerprint means "the same key was entered twice" — the caller answers that with a safe 409,
    not by joining anything.
    """
    key = _resolve_key()   # same production-mandatory secret that guards the token ciphertext
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()
