"""Read-only policy evaluation. Never grants mutation or reconstructs legacy consent."""
from datetime import datetime, timedelta, timezone

POLICY_VERSION = 'normal-close-30d-v1'
RECOVERY_DAYS = 30


def evaluate_closure(*, policy_version, closed_at, now, purge_started=False):
    """Evaluate an explicit future lifecycle record, not users.deleted_at alone.

    Eligible recovery still needs authenticated ownership, a transactional row lock,
    and verified email. Due means erasure REVIEW, not permission to run DELETE.
    No legal-request deadline is replaced by this ordinary-account policy.
    """
    result = {'policy_version': POLICY_VERSION, 'recovery_window_days': RECOVERY_DAYS,
              'state': 'blocked', 'reason': None, 'recovery_eligible': False,
              'recovery_deadline': None, 'erasure_review_due': False,
              'executable': False}
    if policy_version != POLICY_VERSION:
        result['reason'] = 'explicit_lifecycle_record_required'
        return result
    if type(purge_started) is not bool:
        result['reason'] = 'invalid_purge_state'
        return result
    if purge_started:
        result['reason'] = 'purge_already_started'
        return result
    if not isinstance(closed_at, datetime) or not isinstance(now, datetime):
        result['reason'] = 'invalid_clock'
        return result
    try:
        if closed_at.utcoffset() is None or now.utcoffset() is None:
            result['reason'] = 'timezone_required'
            return result
        start = closed_at.astimezone(timezone.utc)
        current = now.astimezone(timezone.utc)
        deadline = start + timedelta(days=RECOVERY_DAYS)
    except (ValueError, OverflowError, TypeError):
        result['reason'] = 'invalid_clock'
        return result
    if current < start:
        result['reason'] = 'closure_in_future'
        return result
    result['recovery_deadline'] = deadline.isoformat()
    if current < deadline:
        result.update(state='recovery_window', recovery_eligible=True)
    else:
        result.update(state='erasure_review_due', erasure_review_due=True)
    return result


def rollout_status():
    """Honest metadata for existing erasure dry-run while persistence is absent."""
    return {'policy_version': POLICY_VERSION, 'recovery_window_days': RECOVERY_DAYS,
            'state': 'not_activated', 'lifecycle_record_required': True,
            'legacy_deleted_at_is_not_policy_acceptance': True,
            'automatic_erasure_implemented': False,
            'legal_requests_have_separate_deadlines': True}
