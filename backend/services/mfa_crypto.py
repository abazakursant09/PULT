"""Encrypt the TOTP shared secret at rest.

The seed used to sit in mfa_secrets.secret as plaintext, so anyone with a DB read (a backup,
a replica, an SQLi elsewhere) could regenerate every user's 2FA codes and walk past the second
factor. Marketplace tokens were already Fernet-encrypted through credential_vault; the TOTP seed
now uses the same vault and the same CRED_ENC_KEY.

Backward compatible: rows written before this change hold a bare base32 seed. On read we try to
decrypt, and a value that is not a Fernet token (a legacy plaintext seed) is returned as-is, so
existing 2FA users keep working. New writes are always encrypted; a legacy row becomes encrypted
the next time the user re-runs /setup.
"""
from services.marketplace import credential_vault


def store_secret(plaintext: str) -> str:
    """Encrypt a TOTP seed for storage. Returns the Fernet token as a str (urlsafe base64)."""
    return credential_vault.encrypt(plaintext).decode()


def load_secret(stored: str) -> str:
    """Return the plaintext TOTP seed from a stored value.

    Encrypted (post-change) rows are decrypted. A legacy plaintext row is not a valid Fernet
    token, so decryption raises and we return the stored value unchanged — the seed itself.
    """
    try:
        return credential_vault.decrypt(stored.encode())
    except ValueError:
        return stored
