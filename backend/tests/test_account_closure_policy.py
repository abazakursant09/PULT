from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from services.account_closure_policy import POLICY_VERSION, evaluate_closure, rollout_status

START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def evaluate(**overrides):
    values = dict(policy_version=POLICY_VERSION, closed_at=START, now=START)
    values.update(overrides)
    return evaluate_closure(**values)


@pytest.mark.parametrize('elapsed', [timedelta(0), timedelta(days=29),
                                     timedelta(days=30)-timedelta(microseconds=1)])
def test_recovery_window(elapsed):
    result = evaluate(now=START+elapsed)
    assert result['recovery_eligible'] is True
    assert result['erasure_review_due'] is False
    assert result['executable'] is False


@pytest.mark.parametrize('elapsed', [timedelta(days=30), timedelta(days=31), timedelta(days=365)])
def test_deadline_is_exclusive_and_does_not_authorize_purge(elapsed):
    result = evaluate(now=START+elapsed)
    assert result['recovery_eligible'] is False
    assert result['erasure_review_due'] is True
    assert result['executable'] is False


@pytest.mark.parametrize('version', [None, '', 'legacy', 'normal-close-30d-v2'])
def test_no_retroactive_policy_from_soft_delete(version):
    result = evaluate(policy_version=version)
    assert result['state'] == 'blocked'
    assert result['recovery_deadline'] is None


@pytest.mark.parametrize('overrides', [
    {'closed_at': None}, {'now': '2026-11-10'},
    {'closed_at': START.replace(tzinfo=None)}, {'now': START.replace(tzinfo=None)},
    {'closed_at': START+timedelta(seconds=1)}, {'purge_started': 'false'},
    {'purge_started': True},
    {'closed_at': datetime.max.replace(tzinfo=timezone.utc), 'now': datetime.max.replace(tzinfo=timezone.utc)},
])
def test_invalid_or_irreversible_state_fails_closed(overrides):
    result = evaluate(**overrides)
    assert result['state'] == 'blocked'
    assert not result['recovery_eligible'] and not result['executable']


def test_timezone_normalization_preserves_deadline():
    result = evaluate(closed_at=START.astimezone(timezone(timedelta(hours=3))))
    assert result['recovery_deadline'] == (START+timedelta(days=30)).isoformat()


def test_rollout_metadata_does_not_claim_activation():
    result = rollout_status()
    assert result['recovery_window_days'] == 30
    assert result['state'] == 'not_activated'
    assert result['automatic_erasure_implemented'] is False


def test_timezone_object_without_offset_is_not_aware():
    class UndefinedOffset(tzinfo):
        def utcoffset(self, dt):
            return None
    result = evaluate(closed_at=START.replace(tzinfo=UndefinedOffset()))
    assert result['state'] == 'blocked'
    assert result['reason'] == 'timezone_required'
