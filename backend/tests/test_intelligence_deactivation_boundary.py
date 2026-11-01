"""Private SQLite boundary proof; all analysis/Telegram transport is synthetic."""
import asyncio
import socket
import threading
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models.insight import InsightRecord
from models.telegram_notification_log import TelegramNotificationLog
from models.user import User
import routers.action_engine as action_engine
import tasks.intelligence_loop as worker


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original_pair, original_connect = socket.socketpair, socket.socket.connect
    local = threading.local()
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Network forbidden in intelligence boundary test")

    def pair(*args, **kwargs):
        local.selfpipe = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            local.selfpipe = False

    def connect(sock, address):
        if (getattr(local, "selfpipe", False) and isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1"}):
            return original_connect(sock, address)
        return deny(sock, address)

    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    yield
    assert not attempts


async def database():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def scenario(monkeypatch, mode="insight", count=1):
    insights = [NS(key=f"sales_growth:{i}", status="active", is_demo=False,
                   product_name=f"Synthetic {i}", marketplace="", impact_score=1)
                for i in range(count)]
    analysis = AsyncMock(return_value=insights)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(action_engine, "_compute_insights", analysis)
    monkeypatch.setattr(worker, "send_message_with_keyboard", sender)
    focus = NS(is_stable=False, root_cause="synthetic", title="Synthetic",
               reason="Synthetic", expected_impact="Synthetic",
               primary_action=None, secondary_action=None)
    monkeypatch.setattr(worker, "compute_operational_focus", lambda *a: focus if mode == "focus" else None)
    pattern = NS(stabilization_complexity="systemic", confidence=80,
                 marketplace="", operational_summary="Synthetic")
    monkeypatch.setattr(worker, "detect_portfolio_patterns", lambda *a, **kw: [pattern] if mode == "portfolio" else [])
    monkeypatch.setattr(worker, "insight_to_summary", lambda i: {})
    for name in vars(worker).copy():
        if name.startswith("_") and name.endswith("_line"):
            monkeypatch.setattr(worker, name, lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_build_op_summary", lambda **kw: NS(
        portfolio_direction="unstable", recurring_patterns=[], narrative_lines=["Synthetic"]))
    monkeypatch.setattr(worker, "_insight_to_dict", lambda *a: {"rule_type": "sales_growth"})
    async def recent(db, uid, key, hours):
        return not (key.startswith("focus:") and mode == "focus"
                    or key == "portfolio:systemic" and mode == "portfolio"
                    or key == "op_summary:daily" and mode == "digest"
                    or key.startswith("insight:") and mode == "insight"
                    or key == "retention:loop" and mode == "retention")
    monkeypatch.setattr(worker, "_was_sent_recently", recent)
    monkeypatch.setattr(worker, "_signal_is_mature", AsyncMock(return_value=True))
    return analysis, sender, NS(notify_insights=True, notify_sales_growth=True,
                                notify_seo_opportunity=True, notify_retention=mode == "retention",
                                retention_inactive_days=3)


async def seed(factory, state="active"):
    # Detached snapshot intentionally remains active while independent sessions change DB truth.
    user = User(id="synthetic-owner", email="synthetic@example.test", name="Synthetic",
                hashed_password="not-a-password", telegram_chat_id="synthetic-not-a-chat-id")
    async with factory() as db:
        if state != "missing":
            db.add(user)
            await db.commit()
            if state == "deleted":
                await db.execute(update(User).values(deleted_at=datetime(2026, 11, 1)))
                await db.commit()
    return user


async def deactivate(factory):
    async with factory() as db:
        await db.execute(update(User).values(deleted_at=datetime(2026, 11, 1)))
        await db.commit()


@pytest.mark.parametrize("state", ["active", "deleted", "missing", "restored"])
def test_actual_database_eligibility_preserves_data(monkeypatch, state):
    async def run():
        engine, factory = await database()
        try:
            user = await seed(factory, "deleted" if state == "restored" else state)
            if state == "restored":
                async with factory() as db:
                    await db.execute(update(User).values(deleted_at=None))
                    await db.commit()
            analysis, sender, settings = scenario(monkeypatch)
            async with factory() as db:
                db.add(InsightRecord(user_id=user.id, insight_key="retained", status="dismissed"))
                await db.commit()
                result = await worker._process_user(user, settings, db)
                assert result == (1 if state in {"active", "restored"} else 0)
                assert analysis.await_count == sender.await_count == result
                assert len((await db.execute(select(InsightRecord))).scalars().all()) == 1
                logs = (await db.execute(select(TelegramNotificationLog))).scalars().all()
                assert len(logs) == result
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["focus", "portfolio", "digest", "insight", "retention"])
def test_deactivation_after_analysis_blocks_every_send_and_marker(monkeypatch, mode):
    async def run():
        engine, factory = await database()
        try:
            user = await seed(factory)
            analysis, sender, settings = scenario(monkeypatch, mode)
            async def compute(*args):
                await deactivate(factory)
                return analysis.return_value
            analysis.side_effect = compute
            async with factory() as db:
                assert await worker._process_user(user, settings, db) == 0
                assert analysis.await_count == 1
                sender.assert_not_awaited()
                assert not (await db.execute(select(TelegramNotificationLog))).scalars().all()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_midbatch_deactivation_stops_next_send_not_completed_marker(monkeypatch):
    async def run():
        engine, factory = await database()
        try:
            user = await seed(factory)
            analysis, sender, settings = scenario(monkeypatch, count=2)
            async def send(*args):
                await deactivate(factory)
                return True
            sender.side_effect = send
            async with factory() as db:
                assert await worker._process_user(user, settings, db) == 1
                assert sender.await_count == analysis.await_count == 1
                logs = (await db.execute(select(TelegramNotificationLog))).scalars().all()
                assert [r.notification_key for r in logs] == ["insight:sales_growth:0"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_deactivation_during_preparation_prevents_new_analysis(monkeypatch):
    async def run():
        engine, factory = await database()
        try:
            user = await seed(factory)
            analysis, sender, settings = scenario(monkeypatch)
            async with factory() as db:
                execute = db.execute
                async def interleave(statement, *args, **kwargs):
                    result = await execute(statement, *args, **kwargs)
                    if any(d.get("entity") is InsightRecord for d in statement.column_descriptions):
                        await deactivate(factory)
                    return result
                monkeypatch.setattr(db, "execute", interleave)
                assert await worker._process_user(user, settings, db) == 0
                analysis.assert_not_awaited()
                sender.assert_not_awaited()
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("mode,fail_check", [("insight", 1), ("insight", 3), ("digest", 3)])
def test_database_error_is_observable_and_stops_batch(monkeypatch, caplog, mode, fail_check):
    async def run():
        engine, factory = await database()
        try:
            user = await seed(factory)
            analysis, sender, settings = scenario(monkeypatch, mode)
            async with factory() as db:
                execute = db.execute
                checks = 0
                async def fail(statement, *args, **kwargs):
                    nonlocal checks
                    if any(d.get("entity") is User for d in statement.column_descriptions):
                        checks += 1
                        if checks == fail_check:
                            raise RuntimeError("synthetic eligibility database failure")
                    return await execute(statement, *args, **kwargs)
                monkeypatch.setattr(db, "execute", fail)
                assert await worker._process_user(user, settings, db) == 0
                sender.assert_not_awaited()
                assert analysis.await_count == (0 if fail_check == 1 else 1)
                assert "eligibility" in caplog.text
                assert not (await execute(select(TelegramNotificationLog))).scalars().all()
        finally:
            await engine.dispose()
    asyncio.run(run())
