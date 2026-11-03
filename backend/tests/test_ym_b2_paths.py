"""B2 publication entry contracts: real executor, synthetic HTTP, owned WAL DB."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace
from routers import reviews
from services.marketplace import credential_vault, ym_review_read_hold
from tasks import auto_publish_reviews as auto
from test_ar4_auto_publish import _seed
from test_marketplace_retry_policy import no_external_io  # noqa: F401


@asynccontextmanager
async def scenario_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'publication.sqlite').as_posix()}",
                                 connect_args={"timeout": .2})
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode=WAL"))).scalar_one() == "wal"
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(auto, "AsyncSessionLocal", factory)
        monkeypatch.setattr(ym_review_read_hold, "AsyncSessionLocal", factory)
        # Test-local policy setup, matching existing auto-publication fixtures.
        monkeypatch.setattr(auto.settings, "automation_enabled", True)
        async with factory() as db:
            uid, review = await _seed(db, marketplace="yandex")
            connection = (await db.execute(select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == uid))).scalar_one()
            wid, aid = str(uuid.uuid4()), str(uuid.uuid4())
            db.add(Workspace(id=wid, owner_user_id=uid))
            db.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="yandex",
                external_account_id="101", identity_status="verified",
                ym_review_read_not_before=datetime(2099, 1, 1, tzinfo=timezone.utc),
                ym_review_read_indeterminate=False))
            connection.workspace_id = wid
            connection.marketplace_account_id = aid
            connection.verification_status = "verified"
            connection.verified_at = datetime.utcnow()
            credential_id, generation = str(uuid.uuid4()), uuid.uuid4().hex
            db.add(ApiCredential(id=credential_id, connection_id=connection.id, scope="feedbacks",
                secret_enc=credential_vault.encrypt("SYNTHETIC"), meta={"account_ref": "101",
                    "ym_secret_generation": generation,
                    "ym_cabinet_proof": {"version": 1, "generation": generation,
                        "account_id": aid, "business_id": "101", "credential_id": credential_id}},
                verification_status="verified", verified_at=datetime.utcnow()))
            review.external_review_id = "42"
            review.publication_attempts = 2
            await db.commit()
            credential = await ym_review_read_hold.load_credential_snapshot(
                db, connection_id=connection.id, scope="feedbacks")
            async with ym_review_read_hold.bind_cabinet_context(db, connection, credential) as binding:
                assert binding is not None, "fixture must prove current-key managed binding, not legacy exclusion"
            yield db, uid, review
    finally:
        await engine.dispose()


def snapshot(review):
    return (review.publication_attempts, review.status, review.response_text,
            review.execution_log_id, review.failure_reason, review.published_at)


def synthetic_transport(monkeypatch):
    sent = []

    class SyntheticHTTP:
        def __init__(self, *, timeout):
            assert timeout is not None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, headers, json, params):
            assert method == "POST"
            assert url == "https://api.partner.market.yandex.ru/v2/businesses/101/goods-feedback/comments/update"
            assert headers["Api-Key"] == "SYNTHETIC" and params is None
            assert json == {"feedbackId": 42, "comment": {"text": "Спасибо!"}}
            sent.append("synthetic-publication")
            return httpx.Response(200, json={"result": {"id": 100, "status": "PUBLISHED"}})

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return sent


def test_manual_held_new_publication_preserves_state_without_claim(tmp_path, monkeypatch):
    async def run():
        async with scenario_db(tmp_path, monkeypatch) as (db, uid, review):
            before = snapshot(review)
            sent = synthetic_transport(monkeypatch)
            status = 200
            try:
                await reviews.publish_review_response(review.product_id, review.id,
                    current_user=await db.get(User, uid), db=db)
            except HTTPException as exc:
                status = exc.status_code
            await db.refresh(review)
            logs = (await db.execute(select(ExecutionLog))).scalars().all()
            actual = {"http_status": status, "state": snapshot(review),
                      "new_claims": len(logs), "outbound": len(sent)}
            assert actual == {"http_status": 429, "state": before, "new_claims": 0, "outbound": 0}

    asyncio.run(run())


def test_auto_held_new_publication_preserves_state_without_claim(tmp_path, monkeypatch):
    async def run():
        async with scenario_db(tmp_path, monkeypatch) as (db, uid, review):
            before = snapshot(review)
            sent = synthetic_transport(monkeypatch)
            outcome = await auto.run_auto_publish_reviews()
            await db.refresh(review)
            logs = (await db.execute(select(ExecutionLog))).scalars().all()
            actual = {"published": outcome["published"], "state": snapshot(review),
                      "new_claims": len(logs), "outbound": len(sent)}
            assert actual == {"published": 0, "state": before, "new_claims": 0, "outbound": 0}
            assert outcome["deferred"] >= 1

    asyncio.run(run())


ERROR_CASES = [
    ({"code": "YM_ADMISSION_DEFERRED", "reason": "hold", "not_before": "2099-01-01T00:00:00+00:00"}, 429),
    ({"code": "YM_ADMISSION_DEFERRED", "reason": "hold", "not_before": None}, 503),
    ({"code": "YM_ADMISSION_AUTHORITY", "reason": "authority", "not_before": None}, 503),
    ({"code": "YM_ADMISSION_IDENTITY", "reason": "identity", "not_before": None}, 409),
    ({"code": "YM_ADMISSION_INDETERMINATE", "reason": "indeterminate", "not_before": "2099-01-01T00:00:00+00:00"}, 503),
    ({"code": "RATE_LIMIT", "reason": "hold", "not_before": "2099-01-01T00:00:00+00:00"}, 429),
    ({"code": "RATE_LIMIT", "reason": "identity", "not_before": None}, 409),
]


def deferred_result(error):
    from services.marketplace.executor import ExecutionResult
    return ExecutionResult(None, "deferred", "publish_review_response", "yandex", error=error)


@pytest.mark.parametrize("error,status", ERROR_CASES)
def test_consumer_generic_http_deferral(error, status):
    from routers._op_http import raise_if_reconcile
    with pytest.raises(HTTPException) as caught:
        raise_if_reconcile(deferred_result(error))
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == error["code"]
    assert caught.value.detail["log_id"] is None
    assert ("Retry-After" in (caught.value.headers or {})) is (status == 429)


@pytest.mark.parametrize("error,status", ERROR_CASES)
def test_consumer_manual_deferral_never_changes_bookkeeping(tmp_path, monkeypatch, error, status):
    async def run():
        async with scenario_db(tmp_path, monkeypatch) as (db, uid, review):
            before = snapshot(review)
            async def execute(**kwargs):
                assert kwargs["db"] is db and kwargs["mode"] == "manual_l3"
                return deferred_result(error)
            monkeypatch.setattr(reviews.executor, "execute", execute)
            with pytest.raises(HTTPException) as caught:
                await reviews.publish_review_response(review.product_id, review.id,
                    current_user=await db.get(User, uid), db=db)
            assert caught.value.status_code == status
            assert ("Retry-After" in (caught.value.headers or {})) is (status == 429)
            await db.refresh(review)
            assert snapshot(review) == before
            assert not (await db.execute(select(ExecutionLog))).scalars().all()
    asyncio.run(run())


@pytest.mark.parametrize("error,status", ERROR_CASES)
def test_consumer_auto_deferral_preserves_attempts_and_projects_retry(tmp_path, monkeypatch, error, status):
    async def run():
        async with scenario_db(tmp_path, monkeypatch) as (db, uid, review):
            before = snapshot(review)
            async def execute(**kwargs):
                assert kwargs["mode"] == "automated_l4" and kwargs["user_id"] == uid
                return deferred_result(error)
            monkeypatch.setattr(auto.executor, "execute", execute)
            outcome = await auto.run_auto_publish_reviews()
            await db.refresh(review)
            assert snapshot(review) == before and outcome["deferred"] == 1
            assert outcome["published"] == 0 and outcome["terminal"] == 0
            assert review.retry_next_at is not None
            if status == 429:
                assert review.retry_next_at == datetime(2099, 1, 1)
            else:
                assert datetime.utcnow() < review.retry_next_at < datetime(2099, 1, 1)
            assert not (await db.execute(select(ExecutionLog))).scalars().all()
    asyncio.run(run())


@pytest.mark.parametrize("error,status", ERROR_CASES)
def test_consumer_resume_denial_never_constructs_applied_audit(monkeypatch, error, status):
    from fastapi import Response
    from routers import internal_recovery as recovery
    async def resume(**kwargs):
        reason = error["reason"] if error["code"] == "RATE_LIMIT" else error["code"]
        return SimpleNamespace(status="deferred", reason_code=reason,
            not_before=error["not_before"], log_id="existing-operation", audit=None, dispatch_attempted=False)
    async def forbidden(*args):
        raise AssertionError("deferred resume must not build a successful audit/projection response")
    monkeypatch.setattr(recovery.operator_resume, "resume", resume)
    monkeypatch.setattr(recovery, "_current_projection", forbidden)
    async def run():
        with pytest.raises(HTTPException) as caught:
            await recovery.authorize_retry("existing-operation", Response(),
                ctx=recovery._OperatorContext("synthetic-operator", "synthetic-user"),
                idem_key=str(uuid.uuid4()), body=None)
        assert caught.value.status_code == status
        assert ("Retry-After" in (caught.value.headers or {})) is (status == 429)
    asyncio.run(run())


def test_consumer_decision_confirm_keeps_200_deferred_false_contract(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from models.decision_apply_intent import DecisionApplyIntent
    from routers import decision_apply as decision_routes
    from database import get_db
    from dependencies import get_current_user
    from services.decision_apply_ux import confirm as confirm_module
    from services.action_binding.execution_bridge import BoundExecutionResult
    from test_decision_apply_ux_confirm import _seed_applyable
    real_http = httpx.AsyncClient
    async def run():
        async with scenario_db(tmp_path, monkeypatch) as (db, uid, review):
            decision_id = await _seed_applyable(db, uid)
            async def preview(*args, **kwargs):
                return SimpleNamespace(applyable=True, action_key="reduce_discount", dry_run_status="dry_run_ok")
            async def execute(*args, **kwargs):
                return BoundExecutionResult(ok=False, decision_id=decision_id, action_key="reduce_discount",
                    payload={}, execution_log_id=None, status="deferred", reason="YM_ADMISSION_DEFERRED")
            async def forbidden(*args):
                raise AssertionError("deferred apply must not open measurement")
            monkeypatch.setattr(confirm_module, "build_apply_preview", preview)
            monkeypatch.setattr(confirm_module, "execute_bound_decision", execute)
            monkeypatch.setattr(confirm_module, "_open_measurement_if_needed", forbidden)
            app = FastAPI()
            app.include_router(decision_routes.router, prefix="/api")
            async def user():
                return await db.get(User, uid)
            async def session():
                yield db
            app.dependency_overrides[get_current_user] = user
            app.dependency_overrides[get_db] = session
            async with real_http(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
                response = await client.post(f"/api/decision-apply/confirm/{decision_id}",
                    json={"marketplace": "wildberries", "sku": "SKU1"})
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is False and body["status"] == "deferred"
            assert body["reason"] == "YM_ADMISSION_DEFERRED" and body["execution_log_id"] is None
            assert body["measurement_opened"] is False and body["intent_id"]
            assert "Retry-After" not in response.headers
            assert len((await db.execute(select(DecisionApplyIntent))).scalars().all()) == 1
    asyncio.run(run())
