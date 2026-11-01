"""No external I/O: active/deleted/missing/restored background boundaries."""
import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.user import User
from models.imported_finance import ImportedFinanceRow
from models.telegram_settings import TelegramSettings
from models.marketplace_connection import MarketplaceConnection
from models.automation_rule import AutomationRule
from models.product import Product

NOW = datetime(2026, 11, 2, 12)


@pytest.fixture
def sample(monkeypatch):
    # Only private SQLite; all named work seams below are intercepted.
    loop = asyncio.new_event_loop()
    def deny_network(*args, **kwargs):
        raise AssertionError("external network forbidden")
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async def setup():
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with factory() as db:
            for uid in ["active", "deleted", "restored"]:
                db.add(User(id=uid, email=f"{uid}@example.invalid", name="Synthetic", hashed_password="synthetic", deleted_at=NOW if uid == "deleted" else None, is_restored=uid == "restored", telegram_chat_id=uid))
            await db.flush()
            for uid in ["active", "deleted", "restored", "missing"]:
                db.add(ImportedFinanceRow(import_id=f"i-{uid}", user_id=uid, marketplace="wildberries", revenue=100, net_profit=50))
                db.add(MarketplaceConnection(id=f"c-{uid}", user_id=uid, marketplace="wildberries", status="connected", scopes=["feedbacks"]))
                db.add(AutomationRule(id=f"r-{uid}", user_id=uid, contour="reputation", action_type="publish_review_response", connection_id=f"c-{uid}", enabled=True, mode="confirm", consent_at=NOW, consent_version="v1"))
                # Existing SQLite fixtures intentionally permit legacy dangling user refs.
                db.add(Product(id=f"p-{uid}", user_id=uid, name="Synthetic", sku=uid, marketplace="wildberries"))
                if uid != "missing":
                    db.add(TelegramSettings(user_id=uid, daily_report=True, daily_report_time="12:00", weekly_summary=True, weekly_summary_day="monday", weekly_summary_time="12:00"))
            await db.commit()
    async def deactivate(uid):
        async with factory() as db:
            await db.execute(update(User).where(User.id == uid).values(deleted_at=NOW))
            await db.commit()
    loop.run_until_complete(setup())
    try:
        yield SimpleNamespace(run=loop.run_until_complete, factory=factory, deactivate=deactivate)
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


@pytest.mark.parametrize("uid,expected", [("active", True), ("deleted", False), ("missing", False), ("restored", True)])
def test_shared_guard_status(sample, uid, expected):
    from services.background_user_guard import is_active_background_user
    async def run():
        async with sample.factory() as db:
            return await is_active_background_user(db, uid)
    assert sample.run(run()) is expected


def test_guard_uses_fresh_scalar_not_loaded_identity_and_propagates_db_error(sample):
    from services.background_user_guard import is_active_background_user
    async def run():
        async with sample.factory() as db:
            loaded = await db.get(User, "active")
            await sample.deactivate("active")
            assert loaded.deleted_at is None
            assert await is_active_background_user(db, "active") is False
        broken = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("synthetic DB unavailable")))
        with pytest.raises(RuntimeError, match="DB unavailable"):
            await is_active_background_user(broken, "active")
    sample.run(run())


@pytest.mark.parametrize("kind", ["daily", "weekly"])
@pytest.mark.parametrize("flip", [False, True])
def test_report_selection_and_final_send_revalidation(sample, monkeypatch, kind, flip):
    from tasks import scheduler as mod
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(mod, "datetime", Clock)
    built, sent = [], []
    async def build(user):
        built.append(user.id)
        if flip:
            await sample.deactivate(user.id)
        return "synthetic" if kind == "daily" else ("synthetic", [])
    async def send(chat, *args):
        sent.append(chat)
        return True
    monkeypatch.setattr(mod, f"_build_{kind}_report", build)
    monkeypatch.setattr(mod, "send_message", send)
    monkeypatch.setattr(mod, "send_message_with_keyboard", send)
    sample.run(getattr(mod, f"_send_{kind}_reports")())
    assert set(built) == {"active", "restored"}
    assert set(sent) == (set() if flip else {"active", "restored"})


def _registry(monkeypatch, callback):
    from services.advisory_runtime import registry
    monkeypatch.setattr(registry, "ADVISORY_PRODUCERS", tuple(
        registry.ProducerSpec(key=key, run=callback, cadence_seconds=1, enabled=True)
        for key in ["one", "two"]
    ))


@pytest.mark.parametrize("flip", [False, True])
def test_scheduled_selection_and_each_producer_gate(sample, monkeypatch, flip):
    from services.advisory_runtime.runtime import AdvisoryRuntime, ProducerResult
    calls = []
    async def producer(ctx):
        calls.append((ctx.user_id, ctx.triggered_by))
        if flip:
            await sample.deactivate(ctx.user_id)
        return ProducerResult(ok=True)
    _registry(monkeypatch, producer)
    async def run():
        async with sample.factory() as db:
            return await AdvisoryRuntime().run_due_producers(db, now=NOW)
    result = sample.run(run())
    assert result.ran == (2 if flip else 4)
    assert {uid for uid, _ in calls} == {"active", "restored"}
    assert all(trigger == "scheduled" for _, trigger in calls)


@pytest.mark.parametrize("uid,expected", [("active", 2), ("restored", 2), ("deleted", 0), ("missing", 0)])
def test_after_import_keeps_result_contract_without_running_ineligible_users(sample, monkeypatch, uid, expected):
    from services.advisory_runtime import after_import
    from services.advisory_runtime.runtime import ProducerResult
    calls = []
    async def producer(ctx):
        calls.append(ctx.user_id)
        return ProducerResult(ok=True)
    _registry(monkeypatch, producer)
    monkeypatch.setattr(after_import, "AsyncSessionLocal", sample.factory)
    assert sample.run(after_import.run_producers_for_user(uid)) == {"ran": expected, "errors": 0}
    assert len(calls) == expected


def test_after_import_db_failure_is_observable_and_never_calls_producer(sample, monkeypatch):
    from services.advisory_runtime import after_import
    callback = AsyncMock()
    _registry(monkeypatch, callback)
    broken = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("synthetic db error")))
    @asynccontextmanager
    async def factory():
        yield broken
    monkeypatch.setattr(after_import, "AsyncSessionLocal", factory)
    result = sample.run(after_import.run_producers_for_user("active"))
    assert result["ran"] == 0 and result["errors"] > 0
    callback.assert_not_awaited()


def test_review_candidates_and_sync_only_active_restored(sample, monkeypatch):
    from tasks import auto_review_pipeline as mod
    from services.marketplace import review_automation_gate as gate
    from config import settings
    monkeypatch.setattr(settings, "automation_enabled", False)
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    monkeypatch.setattr(gate, "get_review_provider", lambda _: SimpleNamespace(supports_reviews=lambda: True))
    seen = []
    async def sync(db, product):
        seen.append(product.user_id)
        return {"imported": 0}
    monkeypatch.setattr(mod.review_ingest, "sync_product_reviews", sync)
    sample.run(mod.run_auto_sync_reviews())
    assert set(seen) == {"active", "restored"}


@pytest.mark.parametrize("uid", ["deleted", "missing"])
def test_ingest_leaf_never_uses_credentials_for_inactive_user(sample, monkeypatch, uid):
    from services.review import ingest
    monkeypatch.setattr(ingest, "get_review_provider", lambda _: SimpleNamespace(supports_reviews=lambda: True))
    forbidden = AsyncMock(side_effect=AssertionError("credential path forbidden"))
    monkeypatch.setattr(ingest, "resolve_feedbacks_connection", forbidden)
    async def run():
        async with sample.factory() as db:
            product = await db.get(Product, f"p-{uid}")
            with pytest.raises(ingest.ReviewSyncNoConnection):
                await ingest.sync_product_reviews(db, product)
    sample.run(run())
    forbidden.assert_not_awaited()


@pytest.mark.parametrize("boundary", ["before_discovery", "after_discovery", "active", "restored"])
def test_ingest_discovery_and_fetch_have_separate_fresh_gates(sample, monkeypatch, boundary):
    from models.api_credential import ApiCredential
    from services.review import ingest
    uid = "restored" if boundary == "restored" else "active"
    fetch = AsyncMock(return_value=[])
    provider = SimpleNamespace(supports_reviews=lambda: True, fetch_reviews=fetch)
    monkeypatch.setattr(ingest, "get_review_provider", lambda _: provider)
    decrypt_calls = []
    monkeypatch.setattr(ingest.credential_vault, "decrypt", lambda _: decrypt_calls.append(uid) or "synthetic")
    original = ingest.resolve_feedbacks_connection
    async def connection(db, user_id, marketplace):
        conn = await original(db, user_id, marketplace)
        if boundary == "before_discovery":
            await sample.deactivate(uid)
        return conn
    monkeypatch.setattr(ingest, "resolve_feedbacks_connection", connection)
    async def discover(provider, token):
        if boundary == "after_discovery":
            await sample.deactivate(uid)
        return "synthetic-account"
    discovery = AsyncMock(side_effect=discover)
    monkeypatch.setattr(ingest, "resolve_account_ref", discovery)
    async def run():
        async with sample.factory() as db:
            db.add(ApiCredential(connection_id=f"c-{uid}", scope="feedbacks", secret_enc=b"synthetic"))
            await db.commit()
            product = await db.get(Product, f"p-{uid}")
            if boundary in ("before_discovery", "after_discovery"):
                with pytest.raises(ingest.ReviewSyncNoConnection):
                    await ingest.sync_product_reviews(db, product)
            else:
                assert (await ingest.sync_product_reviews(db, product))["synced"] == 0
    sample.run(run())
    assert discovery.await_count == (0 if boundary == "before_discovery" else 1)
    assert len(decrypt_calls) == discovery.await_count
    assert fetch.await_count == (1 if boundary in ("active", "restored") else 0)


@pytest.mark.parametrize("uid", ["active", "restored", "deleted", "missing"])
def test_moderation_credential_gate_precedes_decrypt_and_account_discovery(sample, monkeypatch, uid):
    from models.api_credential import ApiCredential
    from tasks import auto_review_pipeline as mod
    decrypt_calls = []
    monkeypatch.setattr(mod.credential_vault, "decrypt", lambda _: decrypt_calls.append(uid) or "synthetic")
    discovery = AsyncMock(return_value="synthetic-account")
    monkeypatch.setattr(mod.review_ingest, "ensure_account_ref", discovery)
    async def run():
        async with sample.factory() as db:
            db.add(ApiCredential(connection_id=f"c-{uid}", scope="feedbacks", secret_enc=b"synthetic"))
            await db.commit()
            conn = await db.get(MarketplaceConnection, f"c-{uid}")
            if uid in ("deleted", "missing"):
                with pytest.raises(mod.review_ingest.ReviewSyncNoConnection):
                    await mod._connection_credential(db, conn, SimpleNamespace())
            else:
                await mod._connection_credential(db, conn, SimpleNamespace())
    sample.run(run())
    assert len(decrypt_calls) == discovery.await_count == (1 if uid in ("active", "restored") else 0)


def test_moderation_rechecks_after_discovery_before_status_dispatch(sample, monkeypatch):
    from models.review_response import ReviewResponse
    from tasks import auto_review_pipeline as mod
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    monkeypatch.setattr(mod, "get_review_provider", lambda _: SimpleNamespace(answer_status=AsyncMock()))
    async def credential(db, conn, provider):
        await sample.deactivate(conn.user_id)
        return "synthetic"
    monkeypatch.setattr(mod, "_connection_credential", credential)
    status = AsyncMock()
    monkeypatch.setattr(mod, "review_answer_status", status)
    async def seed():
        async with sample.factory() as db:
            for uid in ("active", "restored"):
                db.add(ReviewResponse(product_id=f"p-{uid}", marketplace="wildberries", external_review_id=uid, status="awaiting_moderation"))
            await db.commit()
    sample.run(seed())
    assert sample.run(mod.run_reconcile_moderation())["resolved"] == 0
    status.assert_not_awaited()


@pytest.mark.parametrize("flip", [False, True])
def test_measurement_candidates_and_midbatch_revalidation(sample, monkeypatch, flip):
    from models.engine_effect_observation import EngineEffectObservation
    from tasks import measurement_close as mod
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    seen = []
    async def close(db, *, user_id, **kwargs):
        seen.append(user_id)
        if flip:
            for uid in ("active", "restored"):
                await sample.deactivate(uid)
        return SimpleNamespace(closed=1)
    monkeypatch.setattr(mod, "close_effect_measurement", close)
    async def seed():
        async with sample.factory() as db:
            for uid in ("active", "deleted", "restored", "missing"):
                db.add(EngineEffectObservation(user_id=uid, link_id=uid, insight_key="synthetic", metric_key="synthetic"))
            await db.commit()
    sample.run(seed())
    assert sample.run(mod.run_measurement_close()) == (1 if flip else 2)
    assert set(seen).issubset({"active", "restored"})


def test_review_sync_rechecks_between_products(sample, monkeypatch):
    from tasks import auto_review_pipeline as mod
    from services.marketplace import review_automation_gate as gate
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    monkeypatch.setattr(gate, "get_review_provider", lambda _: SimpleNamespace(supports_reviews=lambda: True))
    seen = []
    async def sync(db, product):
        seen.append(product.user_id)
        await sample.deactivate(product.user_id)
        return {"imported": 0}
    monkeypatch.setattr(mod.review_ingest, "sync_product_reviews", sync)
    async def seed():
        async with sample.factory() as db:
            for uid in ("active", "restored"):
                db.add(Product(id=f"second-{uid}", user_id=uid, name="Synthetic", sku="second", marketplace="wildberries"))
            await db.commit()
    sample.run(seed())
    sample.run(mod.run_auto_sync_reviews())
    assert sorted(seen) == ["active", "restored"]


@pytest.mark.parametrize("flip", [False, True])
def test_publisher_selection_and_final_dispatch_gate(sample, monkeypatch, flip):
    from config import settings
    from models.review_response import ReviewResponse
    from tasks import auto_publish_reviews as mod
    monkeypatch.setattr(settings, "automation_enabled", True)
    monkeypatch.setattr(mod, "AsyncSessionLocal", sample.factory)
    monkeypatch.setattr(mod.gate, "get_review_provider", lambda _: SimpleNamespace(supports_reviews=lambda: True))
    seen = []
    original_gate = mod.gate.assert_auto_publish_allowed
    gate_calls = {}
    async def gate(db, rule_id, uid):
        result = await original_gate(db, rule_id, uid)
        gate_calls[uid] = gate_calls.get(uid, 0) + 1
        if flip and gate_calls[uid] == 2:
            await sample.deactivate(uid)
        return result
    monkeypatch.setattr(mod.gate, "assert_auto_publish_allowed", gate)
    async def execute(**kwargs):
        seen.append(kwargs["user_id"])
        return SimpleNamespace(ok=True, result={}, log_id=None)
    monkeypatch.setattr(mod.executor, "execute", execute)
    async def seed():
        async with sample.factory() as db:
            await db.execute(update(AutomationRule).values(mode="auto"))
            for uid in ("active", "restored", "deleted", "missing"):
                db.add(ReviewResponse(product_id=f"p-{uid}", marketplace="wildberries", external_review_id=uid, status="approved", safety_category="SAFE", rating=5, response_text="Synthetic"))
            await db.commit()
    sample.run(seed())
    result = sample.run(mod.run_auto_publish_reviews())
    assert result["published"] == (0 if flip else 2)
    assert sorted(seen) == ([] if flip else ["active", "restored"])
