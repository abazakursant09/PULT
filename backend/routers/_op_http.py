"""SECURITY-2D-1B-B — HTTP glue for operation keys (kept out of the pure services layer).

`resolve_client_key` turns the `Idempotency-Key` header into a `v1:client:<uuid>` key for a manual
executable route, rejecting a forbidden body key and a malformed/missing header with 422. A dry-run
needs no key (no provider dispatch is possible). `raise_if_reconcile` maps the executor's non-dispatching
`needs_reconcile` outcome to HTTP 409 without leaking the prior key/status/payload.
"""
from __future__ import annotations

from fastapi import HTTPException

from services.marketplace import operation_key


def resolve_client_key(header_value: str | None, *, dry_run: bool, body_key=None) -> str | None:
    """`v1:client:<uuid>` from the header, or None for a dry-run. Raises 422 on a forbidden body key,
    a missing header (executable), or a malformed UUID."""
    try:
        operation_key.forbid_body_key(body_key)
        return None if dry_run else operation_key.client_key(header_value)
    except operation_key.OperationKeyError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "detail": e.detail})


def forbid_body_key(body_key) -> None:
    """Reject a deprecated body idempotency_key (422) — the header is the only manual identity source."""
    try:
        operation_key.forbid_body_key(body_key)
    except operation_key.OperationKeyError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "detail": e.detail})


def raise_if_reconcile(res) -> None:
    """Map a non-dispatching claim outcome to HTTP 409 (in-progress / mismatch / legacy / prior-failed)."""
    if res.status == "needs_reconcile":
        err = res.error or {}
        raise HTTPException(
            status_code=409,
            detail={"code": err.get("code", "NEEDS_RECONCILE"),
                    "message": err.get("detail", "проверьте кабинет перед повторной попыткой"),
                    "log_id": res.log_id},
        )
