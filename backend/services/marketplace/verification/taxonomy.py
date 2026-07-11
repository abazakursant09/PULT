"""Credential verification outcomes (F1.2b-a). Classification only — no I/O, no transport.

Every outcome carries EXPLICIT metadata. Nothing here is inferred from the name of a
value, because the two rules that matter most are exactly the ones a name would get wrong:

  * A TEMPORARY failure must never change persisted state. `rate_limited`, `timeout` and
    `marketplace_unavailable` mean "we do not know", not "the credentials are bad". If they
    were allowed to write state, an outage at Wildberries would mark every seller's working
    token invalid and demand they re-enter it.

  * `decrypt_failure` is NOT a credential failure. It happens locally, before any request
    leaves the process, and it means OUR key cannot read OUR ciphertext — most plausibly
    because CRED_ENC_KEY changed (the vault holds a single Fernet key with no rotation
    support, so a new key orphans every stored secret at once). Classifying it as
    `invalid_credentials` would turn a key rotation into a mass "your token is invalid"
    event across the whole product. It is an internal fault and touches no state.

Only an outcome with `changes_state=True` may write ApiCredential.verification_status.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    """Whose fault it is — which decides who must act."""
    CREDENTIAL = "credential"     # the secret is bad: the seller must supply a new one
    CAPABILITY = "capability"     # the secret is fine but does not grant this
    TRANSPORT   = "transport"     # we could not complete the call
    MARKETPLACE = "marketplace"   # the marketplace is failing
    ADAPTER     = "adapter"       # our code/parsing is failing
    INTERNAL    = "internal"      # our infrastructure is failing (e.g. decryption)
    IDENTITY    = "identity"      # ownership of the external cabinet is contested


class VerificationOutcome(str, Enum):
    VERIFIED                = "verified"
    INVALID_CREDENTIALS     = "invalid_credentials"
    REVOKED                 = "revoked"
    MISSING_SCOPE           = "missing_scope"
    TARIFF_RESTRICTED       = "tariff_restricted"
    RATE_LIMITED            = "rate_limited"
    TIMEOUT                 = "timeout"
    MARKETPLACE_UNAVAILABLE = "marketplace_unavailable"
    MALFORMED_RESPONSE      = "malformed_response"
    SCHEMA_MISMATCH         = "schema_mismatch"
    ADAPTER_ERROR           = "adapter_error"
    DECRYPT_FAILURE         = "decrypt_failure"
    VERIFICATION_UNSUPPORTED = "verification_unsupported"
    OWNERSHIP_CONFLICT      = "ownership_conflict"


@dataclass(frozen=True)
class OutcomeMeta:
    permanent: bool          # is this a settled answer, or a "try again later"?
    retryable: bool          # may the same probe be repeated as-is?
    changes_state: bool      # may this write ApiCredential.verification_status?
    error_category: ErrorCategory | None   # None for success


O = VerificationOutcome
C = ErrorCategory

# The single source of truth. Exhaustively tested — every outcome must appear here.
OUTCOME_META: dict[VerificationOutcome, OutcomeMeta] = {
    # ── settled answers: they may write state ────────────────────────────────
    O.VERIFIED:            OutcomeMeta(True,  False, True,  None),
    O.INVALID_CREDENTIALS: OutcomeMeta(True,  False, True,  C.CREDENTIAL),
    O.REVOKED:             OutcomeMeta(True,  False, True,  C.CREDENTIAL),
    O.MISSING_SCOPE:       OutcomeMeta(True,  False, True,  C.CAPABILITY),
    O.TARIFF_RESTRICTED:   OutcomeMeta(True,  False, True,  C.CAPABILITY),
    O.OWNERSHIP_CONFLICT:  OutcomeMeta(True,  False, True,  C.IDENTITY),

    # ── "we do not know": audited, but they must NOT touch state ─────────────
    O.RATE_LIMITED:            OutcomeMeta(False, True,  False, C.TRANSPORT),
    O.TIMEOUT:                 OutcomeMeta(False, True,  False, C.TRANSPORT),
    O.MARKETPLACE_UNAVAILABLE: OutcomeMeta(False, True,  False, C.MARKETPLACE),
    O.MALFORMED_RESPONSE:      OutcomeMeta(False, False, False, C.ADAPTER),
    O.SCHEMA_MISMATCH:         OutcomeMeta(False, False, False, C.ADAPTER),
    O.ADAPTER_ERROR:           OutcomeMeta(False, False, False, C.ADAPTER),

    # ── settled, but still not a statement about the credentials ─────────────
    # decrypt_failure is permanent (retrying the same broken key changes nothing) yet
    # writes no state: the secret may be perfectly valid — we simply cannot read it.
    O.DECRYPT_FAILURE:          OutcomeMeta(True,  False, False, C.INTERNAL),
    # no probe exists for this (marketplace, scope). Honest silence, not a verdict.
    O.VERIFICATION_UNSUPPORTED: OutcomeMeta(True,  False, False, C.CAPABILITY),
}


def meta(outcome: VerificationOutcome) -> OutcomeMeta:
    return OUTCOME_META[outcome]


@dataclass(frozen=True)
class VerificationResult:
    """What a probe returns. Carries no secret and no marketplace response body."""
    outcome: VerificationOutcome
    probe_key: str
    adapter_version: str
    http_status: int | None = None
    retry_after_seconds: int | None = None
    response_schema_status: str | None = None   # ok | missing_fields | unparseable

    @property
    def error_category(self) -> ErrorCategory | None:
        return meta(self.outcome).error_category

    @property
    def changes_state(self) -> bool:
        return meta(self.outcome).changes_state
