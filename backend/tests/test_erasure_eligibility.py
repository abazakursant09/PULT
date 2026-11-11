"""Ordinary closure review only; no purge executor, mail or provider calls."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import event, select

from test_erasure_inventory import inventory_db  # noqa: F401 disposable DB fixture
from models.user import User
from services import account_closure as lifecycle
from services.account_closure_policy import POLICY_VERSION
from services.erasure_inventory import dry_run_account_erasure

START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("scenario,elapsed,state,reason,due", [
    ("active", timedelta(days=31), "blocked", "account_not_closed", False),
    ("restored", timedelta(days=31), "blocked", "account_not_closed", False),
    ("legacy", timedelta(days=31), "blocked", "explicit_lifecycle_record_required", False),
    ("unknown_policy", timedelta(days=31), "blocked", "explicit_lifecycle_record_required", False),
    ("missing_start", timedelta(days=31), "blocked", "invalid_clock", False),
    ("future", -timedelta(microseconds=1), "blocked", "closure_in_future", False),
    ("window", timedelta(days=30)-timedelta(microseconds=1), "recovery_window", None, False),
    ("deadline", timedelta(days=30), "erasure_review_due", None, True),
    ("past_due", timedelta(days=31), "erasure_review_due", None, True),
    ("purge_started", timedelta(days=31), "blocked", "purge_already_started", False),
    ("naive_clock", timedelta(days=31), "blocked", "timezone_required", False),
])
def test_ordinary_erasure_review_is_a_read_only_snapshot(inventory_db, monkeypatch, scenario, elapsed, state, reason, due):
    loop, engine, db, owners = inventory_db
    now = START + elapsed
    monkeypatch.setattr(lifecycle, "utc_now", lambda: now.replace(tzinfo=None) if scenario == "naive_clock" else now)

    async def exercise():
        user = await db.get(User, owners[0])
        user.deleted_at = START.replace(tzinfo=None)
        user.closure_policy_version = POLICY_VERSION
        user.closure_started_at = START
        if scenario in ("active", "restored"):
            user.deleted_at = None
            user.is_restored = scenario == "restored"
        if scenario == "active":
            user.closure_policy_version = user.closure_started_at = None
        elif scenario == "legacy":
            user.closure_policy_version = user.closure_started_at = None
        elif scenario == "unknown_policy":
            user.closure_policy_version = "normal-close-30d-v2"
        elif scenario == "missing_start":
            user.closure_started_at = None
        elif scenario == "purge_started":
            user.closure_purge_started_at = START
        await db.commit()
        fields = (User.deleted_at, User.closure_policy_version, User.closure_started_at,
                  User.closure_restore_requested_at, User.closure_purge_started_at, User.token_version)
        statement = select(*fields).where(User.id == owners[0])
        before = tuple((await db.execute(statement)).one())
        statements = []

        def observe(conn, cursor, sql, parameters, context, many):
            statements.append(sql)

        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        try:
            report = await dry_run_account_erasure(db, owners[0])
            review = report.get("account_closure_review")
            assert isinstance(review, dict), "Account lifecycle review is missing"
            assert review["state"] == state and review["reason"] == reason
            assert review["erasure_review_due"] is due
            assert review["executable"] is False
            assert ("ordinary_closure_not_review_due" in report["blockers"]) is (not due)
            assert report["status"] == "blocked" and report["executable"] is False
            assert report["complete_erasure_ready"] is False
            assert "purge_executor_not_implemented" in report["blockers"]
            assert all(owner not in json.dumps(report) for owner in owners)
            assert await dry_run_account_erasure(db, owners[0]) == report
            assert not db.new and not db.dirty and not db.deleted
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert all("hashed_password" not in sql and "secret_enc" not in sql for sql in statements)
        with db.no_autoflush:
            assert tuple((await db.execute(statement)).one()) == before

    loop.run_until_complete(exercise())


def test_finished_recovery_cannot_be_due_from_retained_closure_history(inventory_db, monkeypatch):
    loop, _, db, owners = inventory_db
    clock = [START + timedelta(days=29)]
    monkeypatch.setattr(lifecycle, "utc_now", lambda: clock[0])

    async def exercise():
        user = await db.get(User, owners[0])
        user.deleted_at = START.replace(tzinfo=None)
        user.closure_policy_version = POLICY_VERSION
        user.closure_started_at = START
        user.closure_restore_requested_at = clock[0]
        lifecycle.finish_recovery(user)
        await db.commit()
        assert user.deleted_at is None and user.closure_started_at == START
        clock[0] = START + timedelta(days=31)
        report = await dry_run_account_erasure(db, owners[0])
        review = report.get("account_closure_review")
        assert isinstance(review, dict), "Account lifecycle review is missing"
        assert review["reason"] == "account_not_closed" and review["erasure_review_due"] is False
        assert review["recovery_deadline"] is None and review["executable"] is False
        assert report["complete_erasure_ready"] is False and report["executable"] is False
        assert not db.new and not db.dirty and not db.deleted

    loop.run_until_complete(exercise())
