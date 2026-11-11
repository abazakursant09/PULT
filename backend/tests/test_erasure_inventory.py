import asyncio
import json
import uuid
from types import SimpleNamespace
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.user import User
from models.workspace import Workspace
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.product import Product
from models.review_response import ReviewResponse
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.marketplace_category import MarketplaceCategoryRow
from models.deal import Deal
from models.supplier_review import SupplierReview
from models.api_sync_state import ApiSyncState
from models.connection_verification_attempt import ConnectionVerificationAttempt
from models.auth_rate_limit_bucket import AuthRateLimitBucket
from services import auth_throttle
from models.automation_rule import AutomationRule
from routers.referrals import _deactivate_account
from routers.connections import create_connection
from schemas.marketplace import ConnectionCreate
from fastapi import HTTPException
from services.account_write_guard import lock_active_account
from sqlalchemy.dialects import postgresql
from services.erasure_inventory import inventory_account, dry_run_account_erasure


@pytest.fixture
def inventory_db():
    loop = asyncio.new_event_loop()
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
        owners = [str(uuid.uuid4()), str(uuid.uuid4())]
        resources = []
        for index, uid in enumerate(owners):
            wid, aid, cid = [str(uuid.uuid4()) for _ in range(3)]
            db.add(User(id=uid, email=f"inventory-{index}@example.com", name="synthetic", hashed_password="SECRET-HASH"))
            db.add(Workspace(id=wid, owner_user_id=uid))
            await db.flush()
            db.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="ozon", identity_status="unverified"))
            db.add(MarketplaceConnection(id=cid, user_id=uid, marketplace="ozon"))
            await db.flush()
            sid, secret_id = str(uuid.uuid4()), str(uuid.uuid4())
            db.add(MarketplaceStore(id=sid, marketplace_account_id=aid, marketplace="ozon", store_key="primary", label="store", source="manual", status="active"))
            db.add(ApiCredential(id=secret_id, connection_id=cid, scope="prices", secret_enc=b"SECRET-CIPHERTEXT"))
            resources.append(dict(account=aid, connection=cid, store=sid, credential=secret_id))
            product = Product(id=str(uuid.uuid4()), user_id=uid, name="synthetic", marketplace="ozon")
            decision = Decision(id=str(uuid.uuid4()), user_id=uid, problem="synthetic")
            db.add_all([product, decision])
            await db.flush()
            db.add(ReviewResponse(product_id=product.id, author="PRIVATE-AUTHOR", review_text="PRIVATE-TEXT"))
            db.add(DecisionOutcome(decision_id=decision.id, metric_name="revenue", expected_window_days=7))
            db.add(Deal(seller_id=uid, supplier_id="shared-counterparty", product_name="synthetic",
                        price_per_unit=10, quantity=1, total_price=10))
            db.add(SupplierReview(reviewer_id=uid, target_type="supplier", target_id="shared-counterparty",
                                  rating=5, text="PRIVATE-REVIEW"))
        db.add(MarketplaceCategoryRow(marketplace="ozon", category_id="global", name="Shared reference"))
        await db.commit()
        db.info["synthetic_resources"] = resources
        return db, owners

    db, owners = loop.run_until_complete(setup())
    yield loop, engine, db, owners
    loop.run_until_complete(db.close())
    loop.run_until_complete(engine.dispose())
    loop.close()


def test_counts_owned_paths_without_selecting_secrets_or_deleting(inventory_db):
    loop, engine, db, owners = inventory_db
    statements = []

    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        report = loop.run_until_complete(inventory_account(db, owners[0]))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    counts = {row["table"]: row["count"] for row in report["counts"]}
    for name in ("users", "workspaces", "marketplace_accounts", "marketplace_stores", "marketplace_connections", "api_credentials"):
        assert counts[name] == 1, name
    assert counts["review_responses"] == 1
    assert counts["decision_outcomes"] == 1
    assert counts["deals"] == 1
    assert counts["supplier_reviews"] == 1
    assert any(item["table"] == "supplier_reviews" for item in report["cross_party_review"])
    assert "marketplace_category_rows" not in counts
    assert "marketplace_category_rows" in report["shared_reference_tables"]
    assert "marketplace_category_rows" not in report["unclassified_tables"]
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert all("secret_enc" not in s and "hashed_password" not in s for s in statements)
    text = json.dumps(report)
    assert "SECRET-" not in text
    assert "PRIVATE-" not in text
    assert owners[0] not in text and owners[1] not in text
    assert report["complete_erasure_ready"] is False
    assert report["unclassified_tables"]
    assert "backups" in report["not_inspected"]
    # Repeat and second owner prove counts are not consumed or rows changed.
    assert loop.run_until_complete(inventory_account(db, owners[0])) == report
    assert loop.run_until_complete(inventory_account(db, owners[1])) == report


def test_missing_account_fails_closed(inventory_db):
    loop, _, db, _ = inventory_db
    with pytest.raises(ValueError, match="Account not found"):
        loop.run_until_complete(inventory_account(db, "missing"))


def test_erasure_dry_run_is_select_only_and_never_authorizes(inventory_db):
    loop, engine, db, owners = inventory_db
    statements = []
    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        a = loop.run_until_complete(dry_run_account_erasure(db, owners[0]))
        b = loop.run_until_complete(dry_run_account_erasure(db, owners[1]))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    for report in (a, b):
        assert report["status"] == "blocked" and report["read_only"]
        assert report["executable"] is False and report["complete_erasure_ready"] is False
        assert "retention_policy_required" in report["blockers"]
        policy = report['ordinary_closure_policy']
        assert policy['recovery_window_days'] == 30
        assert policy['state'] == 'not_activated'
        assert policy['legacy_deleted_at_is_not_policy_acceptance'] is True
        assert policy['automatic_erasure_implemented'] is False
        assert "backups" in report["unverified_stages"]
        assert all(row["disposition"] == "review_only" for row in report["table_reviews"])
        assert not set(report["excluded_shared_tables"]) & {r["table"] for r in report["table_reviews"]}
        encoded = json.dumps(report)
        assert all(uid not in encoded for uid in owners)
        assert "SECRET-" not in encoded and "PRIVATE-" not in encoded
        review = next(r for r in report["table_reviews"] if r["table"] == "supplier_reviews")
        assert "cross_party_review_required" in review["reasons"]
    assert a == b  # counts only; not an owner-bound execution token


def test_erasure_dry_run_reports_mixed_owner_blocker(inventory_db):
    loop, _, db, owners = inventory_db
    a, b = db.info["synthetic_resources"]
    async def exercise():
        db.add(ApiSyncState(marketplace_connection_id=a["connection"],
            marketplace_account_id=a["account"], marketplace_store_id=b["store"], data_type="prices"))
        await db.commit()
        report = await dry_run_account_erasure(db, owners[0])
        assert "conflicting_owner_paths" in report["blockers"]
        assert {"table": "api_sync_states", "count": 1} in report["conflicting_owner_paths"]
        row = next(r for r in report["table_reviews"] if r["table"] == "api_sync_states")
        assert row["observed_owner_rows"] == 0 and "conflicting_owner_paths" in row["reasons"]
        with pytest.raises(ValueError, match="Account not found"):
            await dry_run_account_erasure(db, "missing")
    loop.run_until_complete(exercise())


@pytest.mark.parametrize("reporter", [inventory_account, dry_run_account_erasure])
def test_inventory_does_not_flush_pending_operator_changes(inventory_db, reporter):
    loop, engine, db, owners = inventory_db
    pending = User(email="pending@example.com", name="pending", hashed_password="NOT-WRITTEN")
    db.add(pending)
    statements = []

    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        loop.run_until_complete(reporter(db, owners[0]))
        with pytest.raises(ValueError):
            loop.run_until_complete(reporter(db, "missing"))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    assert pending in db.new
    assert pending.id is None
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    loop.run_until_complete(db.rollback())


@pytest.mark.parametrize("reporter", [inventory_account, dry_run_account_erasure])
def test_legacy_table_missing_from_models_is_flagged_without_reading_content(inventory_db, reporter):
    loop, engine, db, owners = inventory_db

    async def create_legacy():
        await db.execute(text("CREATE TABLE forgotten_private_data (payload TEXT)"))
        await db.execute(text("INSERT INTO forgotten_private_data VALUES ('DO-NOT-READ')"))
        await db.commit()

    loop.run_until_complete(create_legacy())
    statements = []

    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        report = loop.run_until_complete(reporter(db, owners[0]))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    assert "forgotten_private_data" in report["unclassified_tables"]
    assert "DO-NOT-READ" not in json.dumps(report)
    assert not any("from forgotten_private_data" in s.lower() for s in statements)
    assert report["schema_scope"] == "default_schema_tables"


def test_empty_owner_dry_run_still_cannot_authorize_deletion(inventory_db):
    loop, _, db, _ = inventory_db
    async def exercise():
        uid = str(uuid.uuid4())
        db.add(User(id=uid, email=f"{uid}@example.invalid", name="empty", hashed_password="synthetic"))
        await db.commit()
        report = await dry_run_account_erasure(db, uid)
        counts = {row["table"]: row["observed_owner_rows"] for row in report["table_reviews"]}
        assert counts["users"] == 1
        assert all(count == 0 for table, count in counts.items() if table != "users")
        assert report["conflicting_owner_paths"] == []
        assert report["status"] == "blocked" and report["executable"] is False
        assert "purge_executor_not_implemented" in report["blockers"]
        assert "retention_policy_required" in report["blockers"]
    loop.run_until_complete(exercise())


def test_mixed_owner_links_are_conflicts_not_owned_rows(inventory_db):
    loop, _, db, owners = inventory_db
    a, b = db.info["synthetic_resources"]

    async def seed():
        for resource in (a, b):
            db.add(ApiSyncState(marketplace_connection_id=resource["connection"],
                               marketplace_account_id=resource["account"],
                               marketplace_store_id=resource["store"], data_type="prices"))
        db.add(ApiSyncState(marketplace_connection_id=a["connection"],
                           marketplace_account_id=a["account"], marketplace_store_id=b["store"], data_type="prices"))
        await db.commit()

    loop.run_until_complete(seed())
    for uid in owners:
        report = loop.run_until_complete(inventory_account(db, uid))
        assert next(r["count"] for r in report["counts"] if r["table"] == "api_sync_states") == 1
        assert next(r["count"] for r in report["conflicting_owner_paths"] if r["table"] == "api_sync_states") == 1
        assert report["complete_erasure_ready"] is False


def test_nullable_verification_links_allowed_but_foreign_credential_conflicts(inventory_db):
    loop, _, db, owners = inventory_db
    a, b = db.info["synthetic_resources"]

    async def seed():
        for credential in (None, b["credential"]):
            db.add(ConnectionVerificationAttempt(connection_id=a["connection"], credential_id=credential,
                    marketplace="ozon", probe_key="synthetic", adapter_version="test", outcome="unverified"))
        await db.commit()

    loop.run_until_complete(seed())
    report = loop.run_until_complete(inventory_account(db, owners[0]))
    assert next(r["count"] for r in report["counts"] if r["table"] == "connection_verification_attempts") == 1
    assert next(r["count"] for r in report["conflicting_owner_paths"] if r["table"] == "connection_verification_attempts") == 1


def test_ownerless_tables_are_not_treated_as_customer_deletion_targets(inventory_db):
    loop, _, db, owners = inventory_db
    report = loop.run_until_complete(inventory_account(db, owners[0]))
    ownerless = {"auth_rate_limit_buckets", "monitor_events", "promo_codes", "transport_companies"}
    assert ownerless <= set(report["unclassified_tables"])
    assert ownerless == set(report["ownerless_review"])
    assert not ownerless & {r["table"] for r in report["counts"]}
    assert report["complete_erasure_ready"] is False


def test_security_cleanup_preserves_active_blocks_and_unexpired_rows(inventory_db, monkeypatch):
    loop, _, db, _ = inventory_db
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(auth_throttle, "_now", lambda: now)
    monkeypatch.setattr(auth_throttle.settings, "auth_throttle_cleanup_batch", 1)

    async def exercise():
        for key, expires, blocked in (
            ("a" * 64, now - timedelta(hours=1), None),
            ("d" * 64, now - timedelta(hours=1), None),
            ("b" * 64, now - timedelta(hours=1), now + timedelta(hours=1)),
            ("c" * 64, now + timedelta(hours=1), None),
        ):
            db.add(AuthRateLimitBucket(action="login", dimension="identity", key_hash=key,
                window_started_at=now - timedelta(hours=2), attempts=1,
                expires_at=expires, blocked_until=blocked, updated_at=now))
        await db.commit()
        assert await auth_throttle.cleanup(db) == 1
        assert await auth_throttle.cleanup(db) == 1
        assert await auth_throttle.cleanup(db) == 0
        remaining = (await db.execute(select(AuthRateLimitBucket.key_hash))).scalars().all()
        assert set(remaining) == {"b" * 64, "c" * 64}

    loop.run_until_complete(exercise())


def test_deactivation_disarms_only_owner_and_removes_key_material(inventory_db):
    loop, _, db, owners = inventory_db
    a, b = db.info["synthetic_resources"]

    async def exercise():
        for uid, resource in zip(owners, (a, b)):
            db.add(AutomationRule(user_id=uid, contour="reputation", action_type="publish_review_response",
                                  connection_id=resource["connection"], enabled=True))
        db.add(AutomationRule(user_id=owners[0], contour="money", action_type="legacy", enabled=True))
        audit = ConnectionVerificationAttempt(connection_id=a["connection"], credential_id=a["credential"],
            marketplace="ozon", probe_key="synthetic", adapter_version="test", outcome="unverified")
        db.add(audit)
        await db.commit()
        audit_id = audit.id
        user = await db.get(User, owners[0])
        old_version = user.token_version
        await _deactivate_account(db=db, current_user=user)
        await db.refresh(user)
        assert user.deleted_at is not None and user.token_version == old_version + 1
        connection = await db.get(MarketplaceConnection, a["connection"])
        assert connection.status == "revoked"
        credential = await db.get(ApiCredential, a["credential"])
        assert credential.secret_enc == b""  # tombstone preserves audit FK, never old key material
        assert credential.meta == {}
        assert (await db.get(ConnectionVerificationAttempt, audit_id)).credential_id == a["credential"]
        assert all(not r.enabled for r in (await db.execute(select(AutomationRule).where(AutomationRule.user_id == owners[0]))).scalars())
        assert (await db.get(MarketplaceConnection, b["connection"])).status == "connected"
        assert (await db.get(ApiCredential, b["credential"])).secret_enc == b"SECRET-CIPHERTEXT"
        assert (await db.execute(select(AutomationRule).where(AutomationRule.user_id == owners[1]))).scalar_one().enabled

    loop.run_until_complete(exercise())


def test_deactivation_failure_rolls_back_connection_and_key_changes(inventory_db, monkeypatch):
    loop, _, db, owners = inventory_db
    a = db.info["synthetic_resources"][0]

    async def exercise():
        user = await db.get(User, owners[0])
        original_commit = db.commit

        async def fail_commit():
            raise RuntimeError("synthetic commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            await _deactivate_account(db=db, current_user=user)
        monkeypatch.setattr(db, "commit", original_commit)
        await db.rollback()
        user = await db.get(User, owners[0])
        assert user.deleted_at is None and user.token_version == 0
        assert (await db.get(MarketplaceConnection, a["connection"])).status == "connected"
        assert (await db.get(ApiCredential, a["credential"])).secret_enc == b"SECRET-CIPHERTEXT"

    loop.run_until_complete(exercise())


def test_previously_authenticated_request_cannot_reconnect_deleted_account(inventory_db):
    loop, _, db, owners = inventory_db

    async def exercise():
        user = await db.get(User, owners[0])
        stale = SimpleNamespace(id=user.id, token_version=user.token_version)
        await _deactivate_account(db=db, current_user=user)
        with pytest.raises(HTTPException) as error:
            await create_connection(ConnectionCreate(marketplace="ozon", scope="prices",
                ozon_client_id="synthetic", token="synthetic-new-key"), current_user=stale, db=db)
        assert error.value.status_code == 403

    loop.run_until_complete(exercise())


def test_write_guard_reads_fresh_version_not_cached_user(inventory_db):
    loop, _, db, owners = inventory_db

    async def exercise():
        cached_user = await db.get(User, owners[0])
        assert cached_user.token_version == 0
        await db.execute(update(User).where(User.id == owners[0]).values(token_version=1)
                         .execution_options(synchronize_session=False))
        await db.commit()
        assert cached_user.token_version == 0
        with pytest.raises(HTTPException) as error:
            await lock_active_account(db, cached_user)
        assert error.value.status_code == 401

    loop.run_until_complete(exercise())


def test_write_guard_emits_postgresql_row_lock_without_own_commit():
    statements = []

    class RecordingDb:
        no_autoflush = nullcontext()

        async def execute(self, statement):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return SimpleNamespace(first=lambda: SimpleNamespace(deleted_at=None, token_version=0))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(lock_active_account(RecordingDb(), SimpleNamespace(id="synthetic", token_version=0)))
    finally:
        loop.close()
    assert len(statements) == 1
    assert "FOR UPDATE" in statements[0]
    assert "users.deleted_at" in statements[0] and "users.token_version" in statements[0]


def test_stale_request_cannot_enable_automation_after_deactivation(inventory_db):
    from routers.automation import toggle_rule
    loop, _, db, owners = inventory_db

    async def exercise():
        rule = AutomationRule(user_id=owners[0], contour="money", action_type="legacy", enabled=False)
        db.add(rule)
        await db.commit()
        rule_id = rule.id
        user = await db.get(User, owners[0])
        stale = SimpleNamespace(id=user.id, token_version=user.token_version)
        await _deactivate_account(db=db, current_user=user)
        with pytest.raises(HTTPException) as error:
            await toggle_rule(rule_id, current_user=stale, db=db)
        assert error.value.status_code == 403
        await db.refresh(rule)
        assert not rule.enabled

    loop.run_until_complete(exercise())
