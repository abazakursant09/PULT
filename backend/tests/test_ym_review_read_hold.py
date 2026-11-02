"""B1 public-contract tests: synthetic headers and isolated file SQLite only.

No StaticPool: independent commits must use independent physical connections.
The service is imported at fixture execution, not replaced by a fake implementation.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import importlib
from types import SimpleNamespace
import uuid

import httpx
import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from test_marketplace_retry_policy import no_external_io  # noqa: F401


UTC = timezone.utc
FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
SENTINEL = datetime(2020, 1, 1)


def service():
    return importlib.import_module("services.marketplace.ym_review_read_hold")


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def seed(S):
    from models.user import User
    from models.workspace import Workspace
    from models.marketplace_account import MarketplaceAccount as Account
    from models.marketplace_connection import MarketplaceConnection as Connection

    rows = []
    async with S() as db:
        for business in ("101", "202"):
            uid, wid, aid, cid = [str(uuid.uuid4()) for _ in range(4)]
            await db.execute(User.__table__.insert().values(
                id=uid, email=f"{uid}@example.invalid", name="Synthetic",
                hashed_password="not-a-real-password"))
            await db.execute(Workspace.__table__.insert().values(id=wid, owner_user_id=uid))
            await db.execute(Account.__table__.insert().values(
                id=aid, workspace_id=wid, marketplace="yandex", identity_status="verified",
                external_account_id=business, label="Synthetic", updated_at=SENTINEL))
            await db.execute(Connection.__table__.insert().values(
                id=cid, user_id=uid, workspace_id=wid, marketplace_account_id=aid,
                marketplace="yandex", status="connected", scopes=["feedbacks"]))
            rows.append(SimpleNamespace(user_id=uid, workspace_id=wid, account_id=aid,
                                        connection_id=cid, business=business))
        await db.commit()
    return rows


@asynccontextmanager
async def sqlite_db(tmp_path):
    import models  # noqa: F401
    from database import Base
    from models.user import User
    from models.workspace import Workspace
    from models.marketplace_account import MarketplaceAccount
    from models.marketplace_connection import MarketplaceConnection

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'hold.sqlite').as_posix()}",
                                 connect_args={"timeout": 0.05})
    physical = set()
    event.listen(engine.sync_engine, "connect", lambda dbapi, _: physical.add(id(dbapi)))
    try:
        async with engine.begin() as db:
            await db.exec_driver_sql("PRAGMA journal_mode=WAL")
            await db.exec_driver_sql("PRAGMA foreign_keys=ON")
            await db.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                User.__table__, Workspace.__table__, MarketplaceAccount.__table__,
                MarketplaceConnection.__table__]))
        S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        rows = await seed(S)
        yield S, rows, physical
    finally:
        await engine.dispose()


@asynccontextmanager
async def bound(T, S, row, client, *, operation="reviews", account_ref=None, factory=None):
    from models.marketplace_connection import MarketplaceConnection
    async with S() as caller:
        conn = await caller.get(MarketplaceConnection, row.connection_id)
        async with T.bind_review_read_context(
                caller, conn, row.business if account_ref is None else account_ref,
                session_factory=factory or S):
            with T.selected_review_read(row.business, operation, client=client):
                yield caller


async def state(S, row):
    async with S() as db:
        return dict((await db.execute(text(
            "SELECT ym_review_read_not_before, ym_review_read_indeterminate, updated_at, label "
            "FROM marketplace_accounts WHERE id=:id"), {"id": row.account_id})).mappings().one())


def response(headers=(), status=420):
    return httpx.Response(status, headers=headers, json={"synthetic": True})


def run(tmp_path, case):
    async def go():
        async with sqlite_db(tmp_path) as (S, rows, physical):
            await case(service(), S, rows, physical)
    asyncio.run(go())


@pytest.mark.parametrize("header", ["X-RateLimit-Resource-Until", "x-RaTeLiMiT-rEsOuRcE-uNtIl"])
@pytest.mark.parametrize("status_code", [420, 429])
def test_absolute_header_persists_long_bound_without_a_budget_clamp(tmp_path, header, status_code):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([(header, format_datetime(FUTURE, usegmt=True))], status_code))
            assert exc.value.reason == "hold" and utc(exc.value.not_before) == FUTURE
            assert set(exc.value.to_dict()) == {"code", "detail", "retryable"}
            assert exc.value.code == "RATE_LIMIT"
            assert "101" not in exc.value.detail and header not in exc.value.detail
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred):
                await T.admit_selected_read(client)
    run(tmp_path, case)


@pytest.mark.parametrize("headers", [
    [("Retry-After", "2"), ("Retry-After", "2")],
    [("X-RateLimit-Resource-Until", "not-a-date")],
    [("Retry-After", "-1")], [("Retry-After", "1.5")],
    [("Retry-After", "9" * 400)],
    [("Retry-After", "Sunday, 06-Nov-94 08:49:37 GMT")],
    [("X-RateLimit-Resource-Until", "Fri, 31 Feb 2100 00:00:00 GMT")],
    [("X-RateLimit-Resource-Until", format_datetime(FUTURE, usegmt=True)), ("Retry-After", "bad")],
])
def test_explicit_bad_or_duplicate_hint_is_sticky_indeterminate(tmp_path, headers):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response(headers))
            assert exc.value.reason == "indeterminate"
            with pytest.raises(T.YMReviewReadDeferred):
                await T.observe_selected_read(client, response([("Retry-After", "0")]))
        assert (await state(S, rows[0]))["ym_review_read_indeterminate"] in (True, 1)
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "indeterminate"
    run(tmp_path, case)


@pytest.mark.parametrize("headers", [[], [("X-RateLimit-Resource-Remaining", "0")]])
def test_missing_hints_abort_without_a_fabricated_persisted_deadline(tmp_path, headers):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response(headers))
            assert exc.value.not_before is None
        saved = await state(S, rows[0])
        assert saved["ym_review_read_not_before"] is None
        assert not saved["ym_review_read_indeterminate"]
    run(tmp_path, case)


@pytest.mark.parametrize("date", ["Fri, 01 Jan 2100 00:00:00 GMT", "Fri Jan  1 00:00:00 2100"])
def test_supported_retry_after_dates(tmp_path, date):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([("Retry-After", date)]))
            assert utc(exc.value.not_before) == FUTURE
    run(tmp_path, case)


@pytest.mark.parametrize("header,value", [
    ("X-RateLimit-Resource-Until", "Sat, 01 Jan 0000 00:00:00 GMT"),
    ("Retry-After", "Sat, 01 Jan 0000 00:00:00 GMT"),
    ("Retry-After", "Sat Jan  1 00:00:00 0000"),
])
def test_calendar_year_zero_observation_is_durable_sticky_indeterminate(tmp_path, header, value):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([(header, value)]))
            assert exc.value.reason == "indeterminate", "Gregorian year 0000 is invalid, not year 2000"
        assert (await state(S, rows[0]))["ym_review_read_indeterminate"] in (True, 1)
        # A valid later hint must not erase the invalid-calendar evidence.
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([("Retry-After", "0")]))
            assert exc.value.reason == "indeterminate"
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "indeterminate"
        assert (await state(S, rows[0]))["ym_review_read_indeterminate"] in (True, 1)
    run(tmp_path, case)


@pytest.mark.parametrize("year", [1, 68])
@pytest.mark.parametrize("form", ["resource-gmt", "retry-gmt", "retry-asctime"])
def test_explicit_four_digit_low_year_preserves_literal_gregorian_year(tmp_path, year, form):
    expected = datetime(year, 1, 1, tzinfo=UTC)
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[expected.weekday()]
    header = "X-RateLimit-Resource-Until" if form == "resource-gmt" else "Retry-After"
    value = (f"{weekday} Jan  1 00:00:00 {year:04d}" if form == "retry-asctime"
             else f"{weekday}, 01 Jan {year:04d} 00:00:00 GMT")
    async def case(T, S, rows, _):
        from models.marketplace_account import MarketplaceAccount as A
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([(header, value)]))
            assert exc.value.reason == "hold"
            assert utc(exc.value.not_before) == expected, "Four explicit year digits must not use email's two-digit pivot"
        async with S() as db:
            account = await db.get(A, rows[0].account_id)
            assert utc(account.ym_review_read_not_before) == expected
            assert account.ym_review_read_indeterminate is False
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)  # literal 0001/0068 are past, never 2001/2068
    run(tmp_path, case)


def test_seconds_use_full_bound_and_never_floor_receipt_time(tmp_path):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            before = datetime.now(UTC)
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([("Retry-After", "3600")]))
            after = datetime.now(UTC)
            deadline = utc(exc.value.not_before)
            assert deadline >= before + timedelta(seconds=3600)
            assert deadline <= after + timedelta(seconds=3602)  # SQLite conservative second rounding
    run(tmp_path, case)


def test_max_success_and_noop_admission_preserve_all_other_account_fields(tmp_path):
    async def case(T, S, rows, _):
        from models.marketplace_account import MarketplaceAccount as A
        client = object()
        before = await state(S, rows[0])
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)
            assert await state(S, rows[0]) == before
            for date in (FUTURE, FUTURE - timedelta(days=1)):
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.observe_selected_read(client, response([
                        ("X-RateLimit-Resource-Until", format_datetime(date, usegmt=True))]))
            await T.observe_selected_read(client, response([("Retry-After", "bad")], 200))
        async with S() as db:
            account = await db.get(A, rows[0].account_id)
            assert utc(account.ym_review_read_not_before) == FUTURE
            assert account.updated_at == SENTINEL and account.label == "Synthetic"
            assert not account.ym_review_read_indeterminate
    run(tmp_path, case)


def test_context_isolation_wrong_client_nested_selection_and_cancel_restore(tmp_path):
    async def case(T, S, rows, _):
        client, other = object(), object()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held():
            async with bound(T, S, rows[0], client):
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.observe_selected_read(client, response([("Retry-After", "3600")]))
                entered.set()
                await release.wait()
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.admit_selected_read(client)
                await T.admit_selected_read(other)
                with T.selected_review_read(rows[0].business, "comments", client=other):
                    await T.admit_selected_read(client)  # wrong selected client is excluded
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.admit_selected_read(client)  # outer selection was restored
        async def clear():
            await entered.wait()
            async with bound(T, S, rows[1], client, operation="comments"):
                await T.admit_selected_read(client)
            release.set()
        await asyncio.wait_for(asyncio.gather(held(), clear()), 5)
        async def cancelled():
            async with bound(T, S, rows[0], client):
                raise asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await cancelled()
        # No context and explicit selection alone are intentional direct-call exclusions.
        await T.admit_selected_read(client)
        with T.selected_review_read(rows[0].business, "reviews", client=client):
            await T.admit_selected_read(client)
    run(tmp_path, case)


def test_past_bound_aborts_observation_but_does_not_invent_new_wait(tmp_path):
    async def case(T, S, rows, _):
        client = object()
        past = datetime(2020, 1, 1, tzinfo=UTC)
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([
                    ("X-RateLimit-Resource-Until", format_datetime(past, usegmt=True))]))
            assert exc.value.reason == "hold"
            await T.admit_selected_read(client)
    run(tmp_path, case)


def test_selected_business_mismatch_and_workspace_transfer_are_not_exclusions(tmp_path):
    async def case(T, S, rows, _):
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                with T.selected_review_read("999", "reviews", client=client):
                    await T.admit_selected_read(client)
            assert exc.value.reason == "identity"
            async with S() as changer:
                await changer.execute(text("UPDATE marketplace_accounts SET workspace_id=:wid WHERE id=:id"),
                                      {"wid": rows[1].workspace_id, "id": rows[0].account_id})
                await changer.commit()
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "identity"
    run(tmp_path, case)


def test_uncertain_independent_commit_never_grants_admission(tmp_path):
    async def case(T, S, rows, _):
        from sqlalchemy.exc import OperationalError
        client = object()
        closed = []
        @asynccontextmanager
        async def broken_factory():
            async with S() as db:
                async def broken_commit():
                    raise OperationalError("synthetic commit failure", {}, Exception("synthetic"))
                db.commit = broken_commit
                try:
                    yield db
                finally:
                    closed.append(True)
        async with bound(T, S, rows[0], client, factory=broken_factory):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "authority"
        assert closed == [True]
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)
    run(tmp_path, case)


def test_managed_identity_mismatch_and_under_lock_change_fail_closed(tmp_path):
    async def case(T, S, rows, _):
        from models.marketplace_account import MarketplaceAccount as A
        client = object()
        with pytest.raises(T.YMReviewReadDeferred) as exc:
            async with bound(T, S, rows[0], client, account_ref="999"):
                await T.admit_selected_read(client)
        assert exc.value.reason == "identity"
        async with bound(T, S, rows[0], client):
            async with S() as changer:
                await changer.execute(update(A).where(A.id == rows[0].account_id).values(identity_status="unverified"))
                await changer.commit()
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "identity"
            with pytest.raises(T.YMReviewReadDeferred):
                await T.observe_selected_read(client, response([("Retry-After", "3600")]))
        assert (await state(S, rows[0]))["ym_review_read_not_before"] is None
    run(tmp_path, case)


def test_explicit_unverified_exclusion_does_not_freeze_bootstrap(tmp_path):
    async def case(T, S, rows, _):
        from models.marketplace_account import MarketplaceAccount as A
        async with S() as db:
            await db.execute(update(A).where(A.id == rows[0].account_id).values(identity_status="unverified"))
            await db.commit()
        client = object()
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)
            await T.observe_selected_read(client, response([("Retry-After", "3600")]))
        assert (await state(S, rows[0]))["ym_review_read_not_before"] is None
    run(tmp_path, case)


@pytest.mark.parametrize("identity_status", ["unverified", "unverified_legacy"])
@pytest.mark.parametrize("workspace", ["null", "mismatch"])
def test_legacy_binder_exclusion_precedes_workspace_checks(tmp_path, identity_status, workspace):
    async def case(T, S, rows, _):
        client, called = object(), []
        @asynccontextmanager
        async def forbidden_factory():
            called.append(True)
            raise AssertionError("Excluded legacy binder must not call admission factory")
            yield  # pragma: no cover — keeps the intended async-context factory shape
        async with S() as db:
            await db.execute(text("UPDATE marketplace_accounts SET identity_status=:status WHERE id=:id"),
                             {"status": identity_status, "id": rows[0].account_id})
            await db.execute(text("UPDATE marketplace_connections SET workspace_id=:wid WHERE id=:id"),
                             {"wid": None if workspace == "null" else rows[1].workspace_id,
                              "id": rows[0].connection_id})
            await db.commit()
        async with bound(T, S, rows[0], client, factory=forbidden_factory):
            await T.admit_selected_read(client)
            await T.observe_selected_read(client, response([("Retry-After", "3600")]))
        assert called == []
        assert (await state(S, rows[0]))["ym_review_read_not_before"] is None
    run(tmp_path, case)


@pytest.mark.parametrize("workspace", ["null", "mismatch"])
def test_verified_binder_workspace_negative_controls_still_deny(tmp_path, workspace):
    async def case(T, S, rows, _):
        called = []
        @asynccontextmanager
        async def forbidden_factory():
            called.append(True)
            raise AssertionError("Identity rejection must precede admission factory")
            yield  # pragma: no cover
        async with S() as db:
            await db.execute(text("UPDATE marketplace_connections SET workspace_id=:wid WHERE id=:id"),
                             {"wid": None if workspace == "null" else rows[1].workspace_id,
                              "id": rows[0].connection_id})
            await db.commit()
        with pytest.raises(T.YMReviewReadDeferred) as exc:
            async with bound(T, S, rows[0], object(), factory=forbidden_factory):
                pytest.fail("Verified identity mismatch must not enter excluded context")
        assert exc.value.reason == "identity" and called == []
    run(tmp_path, case)


@pytest.mark.parametrize("status", ["corrupt-status", "Verified"])
def test_corrupt_identity_status_is_not_silent_legacy_exclusion(tmp_path, status):
    async def case(T, S, rows, _):
        async with S() as db:
            await db.execute(text("UPDATE marketplace_accounts SET identity_status=:status WHERE id=:id"),
                             {"status": status, "id": rows[0].account_id})
            await db.commit()
        with pytest.raises(T.YMReviewReadDeferred) as exc:
            async with bound(T, S, rows[0], object()):
                pytest.fail("Unknown/corrupt identity status must not become an unmanaged bypass")
        assert exc.value.reason == "authority"
    run(tmp_path, case)


@pytest.mark.parametrize("fault", ["missing-row", "database-error"])
def test_binder_missing_account_or_lookup_failure_remains_fail_closed(tmp_path, monkeypatch, fault):
    async def case(T, S, rows, _):
        from models.marketplace_connection import MarketplaceConnection
        from sqlalchemy.exc import OperationalError
        async with S() as db:
            conn = await db.get(MarketplaceConnection, rows[0].connection_id)
            if fault == "missing-row":
                conn.marketplace_account_id = str(uuid.uuid4())  # no autoflush into the owned DB
            else:
                async def broken_execute(*args, **kwargs):
                    raise OperationalError("synthetic binder lookup failure", {}, Exception("synthetic"))
                monkeypatch.setattr(db, "execute", broken_execute)
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                async with T.bind_review_read_context(db, conn, rows[0].business, session_factory=S):
                    pytest.fail("Missing authority must not enter exclusion")
            assert exc.value.reason == "authority"
    run(tmp_path, case)


def test_independent_commit_survives_caller_rollback_on_real_separate_connection(tmp_path):
    async def case(T, S, rows, physical):
        client = object()
        async with bound(T, S, rows[0], client) as caller:
            with pytest.raises(T.YMReviewReadDeferred):
                await T.observe_selected_read(client, response([("Retry-After", "3600")]))
            await caller.rollback()
        assert len(physical) >= 2, "StaticPool cannot prove independent commits"
        assert (await state(S, rows[0]))["ym_review_read_not_before"] is not None
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred):
                await T.admit_selected_read(client)
    run(tmp_path, case)


def test_caller_write_lock_observation_failure_never_commits_caller(tmp_path):
    async def case(T, S, rows, physical):
        client = object()
        async with bound(T, S, rows[0], client) as caller:
            await caller.execute(text("UPDATE marketplace_accounts SET label='UNCOMMITTED' WHERE id=:id"),
                                 {"id": rows[0].account_id})
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([("Retry-After", "3600")]))
            assert exc.value.reason == "authority"
            await caller.rollback()
        saved = await state(S, rows[0])
        assert saved["label"] == "Synthetic" and saved["ym_review_read_not_before"] is None
        assert len(physical) >= 2
        # Cleanup released the failed observer lock/session; a later managed attempt can proceed.
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)
    run(tmp_path, case)


def test_synthetic_authority_clock_exact_equality_and_relative_rounding(tmp_path, monkeypatch):
    async def case(T, S, rows, _):
        clock = [FUTURE - timedelta(microseconds=1)]
        broken = [False]
        engine = S.kw["bind"].sync_engine
        def synthetic_clock(conn, cursor, statement, parameters, context, executemany):
            # Inject only this disposable SQLite engine's authoritative UTC query;
            # no dependency on a private service helper or Python wall-clock name.
            sql = statement.lower()
            if sql.lstrip().startswith("select") and "strftime(" in sql and "'now'" in sql:
                if broken[0]:
                    raise ValueError("synthetic clock failure")
                return "SELECT ?", (clock[0].replace(tzinfo=None).isoformat(sep=" "),)
            return statement, parameters
        event.listen(engine, "before_cursor_execute", synthetic_clock, retval=True)
        client = object()
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred):
                await T.observe_selected_read(client, response([
                    ("X-RateLimit-Resource-Until", format_datetime(FUTURE, usegmt=True))]))
            with pytest.raises(T.YMReviewReadDeferred):
                await T.admit_selected_read(client)
            clock[0] = FUTURE
            await T.admit_selected_read(client)  # exact equality is admissible
            clock[0] = FUTURE + timedelta(microseconds=700000)
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.observe_selected_read(client, response([("Retry-After", "3600")]))
            assert utc(exc.value.not_before) >= clock[0] + timedelta(seconds=3600)
        # Clock failure after acquiring authority never becomes an empty/allowed result.
        broken[0] = True
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "authority"
        event.remove(engine, "before_cursor_execute", synthetic_clock)
    run(tmp_path, case)
