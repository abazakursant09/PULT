"""Real row-lock proof in a unique schema; no application data is modified."""
import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from services.account_write_guard import lock_active_account


@pytest.mark.parametrize("deactivation_first", [False, True])
def test_guard_serializes_both_transaction_orders(deactivation_first):
    url = os.environ.get("PULT_ACCOUNT_GUARD_TEST_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL guard test URL not supplied")

    async def exercise():
        schema = "guard_" + uuid.uuid4().hex
        admin = create_async_engine(url)
        engine = None
        try:
            async with admin.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'CREATE TABLE "{schema}".users '
                    '(id varchar(36) PRIMARY KEY, deleted_at timestamp, token_version integer NOT NULL)'))
                await conn.execute(text(f'INSERT INTO "{schema}".users VALUES (\'synthetic\', NULL, 0)'))
            engine = create_async_engine(url, connect_args={"server_settings": {
                "search_path": schema, "statement_timeout": "10000"}})
            stale = SimpleNamespace(id="synthetic", token_version=0)
            async with AsyncSession(engine) as first, AsyncSession(engine) as second:
                await lock_active_account(first, stale)
                first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
                second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
                pending = asyncio.create_task(lock_active_account(second, stale))
                try:
                    async def wait_for_lock():
                        while True:
                            async with admin.connect() as observer:
                                blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid})
                            if first_pid in blockers:
                                return
                            assert not pending.done(), "Second guard bypassed first transaction"
                            await asyncio.sleep(0.02)
                    await asyncio.wait_for(wait_for_lock(), timeout=5)
                    if deactivation_first:
                        await first.execute(text("UPDATE users SET deleted_at=CURRENT_TIMESTAMP, token_version=1"))
                    await first.commit()
                    if deactivation_first:
                        with pytest.raises(HTTPException) as error:
                            await asyncio.wait_for(pending, timeout=5)
                        assert error.value.status_code == 403
                    else:
                        await asyncio.wait_for(pending, timeout=5)
                        await second.execute(text("UPDATE users SET deleted_at=CURRENT_TIMESTAMP, token_version=1"))
                        await second.commit()
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                    await first.rollback()
                    await second.rollback()
            async with AsyncSession(engine) as restored:
                await restored.execute(text("UPDATE users SET deleted_at=NULL"))
                await restored.commit()
                with pytest.raises(HTTPException) as error:
                    await lock_active_account(restored, stale)
                assert error.value.status_code == 401
                await restored.rollback()
                await lock_active_account(restored, SimpleNamespace(id="synthetic", token_version=1))
                await restored.rollback()
        finally:
            if engine is not None:
                await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize("deactivation_first", [False, True])
@pytest.mark.parametrize("writer_kind", ["connection", "automation_toggle"])
def test_actual_connection_and_deactivation_race(monkeypatch, deactivation_first, writer_kind):
    from database import Base
    import models  # noqa: F401
    from models.user import User
    from models.workspace import Workspace
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    from models.automation_rule import AutomationRule
    from routers import connections, referrals, automation
    from schemas.marketplace import ConnectionCreate
    from sqlalchemy import select

    url = os.environ.get("PULT_ACCOUNT_GUARD_TEST_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL guard test URL not supplied")

    async def exercise():
        schema = "routes_" + uuid.uuid4().hex
        uid = str(uuid.uuid4())
        admin = create_async_engine(url)
        engine = None
        acquired, release = asyncio.Event(), asyncio.Event()
        # The real filesystem is outside this DB test's scope.
        monkeypatch.setattr(referrals, "_remove_upload_dir", lambda _: None)
        async def paused_guard(db, user):
            await lock_active_account(db, user)
            acquired.set()
            await release.wait()
        writer_router = connections if writer_kind == "connection" else automation
        monkeypatch.setattr(referrals if deactivation_first else writer_router,
                            "lock_active_account", paused_guard)
        tasks = []
        try:
            async with admin.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(url, connect_args={"server_settings": {
                "search_path": schema, "statement_timeout": "10000"}})
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as seed:
                seed.add(User(id=uid, email=f"{uid}@example.invalid", name="synthetic", hashed_password="not-a-password"))
                await seed.flush()
                seed.add(Workspace(id=str(uuid.uuid4()), owner_user_id=uid))
                rule = AutomationRule(user_id=uid, contour="money", action_type="legacy", enabled=False)
                seed.add(rule)
                await seed.commit()
                rule_id = rule.id
            async with AsyncSession(engine, expire_on_commit=False) as writer, AsyncSession(engine, expire_on_commit=False) as deleter:
                user = await deleter.get(User, uid)
                stale = SimpleNamespace(id=uid, token_version=0)
                writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
                deleter_pid = await deleter.scalar(text("SELECT pg_backend_pid()"))
                async def connect():
                    try:
                        if writer_kind == "automation_toggle":
                            return await automation.toggle_rule(rule_id, current_user=stale, db=writer)
                        return await connections.create_connection(ConnectionCreate(marketplace="ozon", scope="prices",
                            ozon_client_id="synthetic", token="synthetic-key"), current_user=stale, db=writer)
                    finally:
                        await writer.rollback()
                async def deactivate():
                    try:
                        return await referrals._deactivate_account(db=deleter, current_user=user)
                    finally:
                        await deleter.rollback()
                first = asyncio.create_task(deactivate() if deactivation_first else connect())
                tasks.append(first)
                await asyncio.wait_for(acquired.wait(), timeout=10)
                second = asyncio.create_task(connect() if deactivation_first else deactivate())
                tasks.append(second)
                async def blocked():
                    while True:
                        async with admin.connect() as observer:
                            blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"),
                                {"pid": writer_pid if deactivation_first else deleter_pid})
                        if (deleter_pid if deactivation_first else writer_pid) in blockers:
                            return
                        assert not second.done(), "Route bypassed account lock"
                        await asyncio.sleep(.02)
                try:
                    await asyncio.wait_for(blocked(), timeout=5)
                finally:
                    release.set()
                await asyncio.wait_for(first, timeout=10)
                if deactivation_first:
                    with pytest.raises(HTTPException) as error:
                        await asyncio.wait_for(second, timeout=10)
                    assert error.value.status_code == 403
                else:
                    await asyncio.wait_for(second, timeout=10)
            async with AsyncSession(engine) as check:
                user = await check.get(User, uid)
                assert user.deleted_at is not None and user.token_version == 1
                rows = (await check.execute(select(MarketplaceConnection))).scalars().all()
                keys = (await check.execute(select(ApiCredential))).scalars().all()
                expected = 1 if writer_kind == "connection" and not deactivation_first else 0
                assert len(rows) == len(keys) == expected
                assert all(row.status == "revoked" for row in rows)
                assert all(key.secret_enc == b"" and key.meta == {} for key in keys)
                assert not (await check.get(AutomationRule, rule_id)).enabled
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if engine is not None:
                await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(exercise())
