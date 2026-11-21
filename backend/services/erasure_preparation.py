"""Unwired transactional DRAFT preparation. No journal/credential/network IO.

Caller supplies an authorized account identity and owns the transaction. The
fresh locked closure record is an eligibility review, never purge permission.
IDs returned before commit are tentative; application rollback is not an
independent journal identity or durable external-operation receipt.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models.erasure_preparation import ErasurePreparation
from models.user import User
from services import account_closure as lifecycle


class PreparationError(Exception):
    """Fixed secret-free reason; caller must roll back after storage failures."""


@dataclass(frozen=True)
class DraftPreparation:
    request_id: str = field(repr=False)
    subject_candidate_id: str = field(repr=False)
    status: str = field(default="DRAFT_ONLY_CALLER_COMMIT_REQUIRED", init=False)
    subject_enrolled: bool = field(default=False, init=False)
    journal_dispatch_admitted: bool = field(default=False, init=False)
    purge_or_restore_admitted: bool = field(default=False, init=False)


def _uuid4(value):
    if type(value) is not str:
        raise PreparationError("canonical_account_uuid_required")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise PreparationError("canonical_account_uuid_required") from None
    if parsed.version != 4 or str(parsed) != value:
        raise PreparationError("canonical_account_uuid_required")


def _utc(value):
    # These model columns are written in UTC; SQLite returns them without tzinfo.
    if not isinstance(value, datetime):
        raise PreparationError("invalid_closure_epoch")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        if value.utcoffset() is None:
            raise PreparationError("invalid_closure_epoch")
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        raise PreparationError("invalid_closure_epoch") from None


async def prepare_erasure_draft(db, user_id: str) -> DraftPreparation:
    """Flush one DRAFT in a caller-owned transaction; never commit or dispatch.

Only the server creates request IDs. There is no caller-supplied event, subject,
phase, timestamp, state, retry ticket or acknowledgement. No ordinary closure or
recovery endpoint calls this function. A clean session avoids flushing unrelated
pending work or overwriting dirty ORM state while refreshing the locked record.
"""
    _uuid4(user_id)
    if not db.in_transaction():
        raise PreparationError("caller_transaction_required")
    if db.new or db.dirty or db.deleted:
        raise PreparationError("clean_session_required")
    try:
        with db.no_autoflush:
            # Scalar columns bypass a possibly stale ORM User identity-map entry.
            # Do not SELECT name, email, password, IP, tokens or report contents.
            statement = select(
                User.id, User.deleted_at, User.closure_policy_version,
                User.closure_started_at, User.closure_restore_requested_at,
                User.closure_purge_started_at,
            ).where(User.id == user_id).with_for_update(of=User)
            user = (await db.execute(statement)).one_or_none()
            if user is None:
                raise PreparationError("account_not_found")
            review = lifecycle.review_erasure_eligibility(user)
            if review["erasure_review_due"] is not True:
                raise PreparationError("ordinary_closure_not_review_due")
            epoch = _utc(user.closure_started_at)
            existing = (await db.execute(
                select(ErasurePreparation).where(ErasurePreparation.user_id == user_id)
                .with_for_update().execution_options(populate_existing=True)
            )).scalar_one_or_none()
            if existing is not None:
                if (existing.state != "DRAFT"
                        or existing.closure_policy_version != user.closure_policy_version
                        or _utc(existing.closure_started_at) != epoch):
                    raise PreparationError("earlier_preparation_requires_review")
                _uuid4(existing.id)
                return DraftPreparation(existing.id, user_id)
            now = lifecycle.utc_now()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise PreparationError("timezone_aware_clock_required")
            row = ErasurePreparation(
                id=str(uuid4()), user_id=user_id,
                closure_policy_version=user.closure_policy_version,
                closure_started_at=epoch, prepared_at=now.astimezone(timezone.utc),
                state="DRAFT",
            )
            db.add(row)
            await db.flush([row])
            return DraftPreparation(row.id, user_id)
    except SQLAlchemyError:
        # No retry, automatic rollback, second ID or raw SQL/parameters in error.
        raise PreparationError("preparation_storage_failed") from None
