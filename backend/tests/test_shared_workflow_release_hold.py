"""Shared-workflow hold: dispatch barriers and mutation-free read contracts."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.sql import Select

from config import Settings, settings
from database import get_db
from dependencies import get_current_user
from routers import deals, referrals, success_stories, supplier_reviews


DETAIL = (
    "В тестовом режиме доступны CSV, аналитика, экспорт и чтение. "
    "Изменение связанных записей временно отключено."
)
FLAG = "shared_workflows_read_only"
CASES = [
    (deals, "POST", "/deals", {"supplier_id": "synthetic", "product_name": "synthetic",
                              "price_per_unit": 10, "quantity": 1}),
    (deals, "PATCH", "/deals/synthetic/status", {"status": "agreed"}),
    (deals, "POST", "/deals/synthetic/sign", {}),
    (success_stories, "POST", "/success-stories", {"title": "synthetic", "text": "synthetic"}),
    (supplier_reviews, "POST", "/supplier-reviews",
     {"target_type": "supplier", "target_id": "synthetic", "rating": 5}),
    (referrals, "POST", "/referrals/mark-paid/synthetic", {}),
]
CASE_IDS = ["deal-create", "deal-status", "deal-sign", "story", "review", "mark-paid"]


def app_for(module, *, db=None, owner=None, touched=None):
    app = FastAPI()
    app.include_router(module.router)

    async def database():
        if touched is not None:
            touched.append("db")
            raise HTTPException(418, "synthetic dependency barrier")
        yield db

    async def auth():
        if touched is not None:
            touched.append("auth")
            raise HTTPException(418, "synthetic dependency barrier")
        return owner

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = auth
    return app


@pytest.mark.parametrize("module,method,path,body", CASES, ids=CASE_IDS)
@pytest.mark.parametrize("flag", [True, None, 0, "false"],
                         ids=["true", "none", "zero", "text-false"])
def test_held_writes_stop_before_auth_and_db(monkeypatch, module, method, path, body, flag):
    monkeypatch.setitem(settings.__dict__, FLAG, flag)
    touched = []
    with TestClient(app_for(module, touched=touched)) as client:
        response = client.request(method, path, json={**body, FLAG: False},
                                  params={FLAG: "false"})
    assert response.status_code == 403
    assert response.json() == {"detail": DETAIL}
    assert touched == []


@pytest.mark.parametrize("module,method,path,body", CASES, ids=CASE_IDS)
def test_explicit_false_preserves_write_dispatch(monkeypatch, module, method, path, body):
    monkeypatch.setitem(settings.__dict__, FLAG, False)
    touched = []
    with TestClient(app_for(module, touched=touched)) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 418
    assert touched
    assert set(touched) <= {"auth", "db"}


class SelectOnlyDB:
    def __init__(self, records=()):
        self.records = list(records)
        self.selects = 0
        self.gets = 0
        self.referrer = None
        self.autoflush_suppressed = False
        self.no_autoflush_entries = 0
        self.require_no_autoflush = False
        self.add = Mock(side_effect=AssertionError("unexpected add"))
        self.flush = AsyncMock(side_effect=AssertionError("unexpected flush"))
        self.commit = AsyncMock(side_effect=AssertionError("unexpected commit"))
        self.refresh = AsyncMock(side_effect=AssertionError("unexpected refresh"))

    @property
    def no_autoflush(self):
        return self._no_autoflush()

    @contextmanager
    def _no_autoflush(self):
        self.no_autoflush_entries += 1
        self.autoflush_suppressed = True
        try:
            yield
        finally:
            self.autoflush_suppressed = False

    async def execute(self, statement):
        assert isinstance(statement, Select)
        if self.require_no_autoflush:
            assert self.autoflush_suppressed
        self.selects += 1
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.records))

    async def get(self, model, key):
        assert model is referrals.User and key == "synthetic-referrer"
        if self.require_no_autoflush:
            assert self.autoflush_suppressed
        self.gets += 1
        return self.referrer

    def assert_read_only(self):
        assert self.selects > 0
        for spy in (self.add, self.flush, self.commit, self.refresh):
            spy.assert_not_called()


@pytest.mark.parametrize("module,path", [
    (deals, "/deals/my"), (success_stories, "/success-stories"),
    (supplier_reviews, "/supplier-reviews/supplier/synthetic"),
])
def test_held_lists_remain_select_only(monkeypatch, module, path):
    monkeypatch.setitem(settings.__dict__, FLAG, True)
    db = SelectOnlyDB()
    owner = SimpleNamespace(id="synthetic-owner")
    with TestClient(app_for(module, db=db, owner=owner)) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.json() == []
    db.assert_read_only()


def referral_fixture(code):
    owner = SimpleNamespace(id="synthetic-owner", referral_code=code, referred_by_id=None)
    records = [
        SimpleNamespace(is_paid=True, is_valid=True, invalidated_at=None),
        SimpleNamespace(is_paid=True, is_valid=False, invalidated_at=None),
        SimpleNamespace(is_paid=True, is_valid=False, invalidated_at=datetime(2026, 1, 1)),
        SimpleNamespace(is_paid=False, is_valid=False, invalidated_at=None),
        SimpleNamespace(is_paid=False, is_valid=True, invalidated_at=None),
    ]
    return owner, SelectOnlyDB(records)


def patch_referral_helpers(monkeypatch, *, held):
    helpers = []
    for name, value in [("_ensure_code", "LEGACY"), ("_revalidate_records", 3),
                        ("_check_milestones", None)]:
        mock = AsyncMock(side_effect=AssertionError("held helper called")) if held else AsyncMock(return_value=value)
        monkeypatch.setattr(referrals, name, mock)
        helpers.append(mock)
    return helpers


@pytest.mark.parametrize("code", [None, "STORED"])
@pytest.mark.parametrize("has_referrer", [False, True])
def test_held_referral_stats_use_stored_values_without_writes(monkeypatch, code, has_referrer):
    monkeypatch.setitem(settings.__dict__, FLAG, True)
    helpers = patch_referral_helpers(monkeypatch, held=True)
    owner, db = referral_fixture(code)
    if has_referrer:
        owner.referred_by_id = "synthetic-referrer"
        db.referrer = SimpleNamespace(email="referrer@example.invalid")
    db.require_no_autoflush = True
    before = [vars(owner).copy(), *[vars(record).copy() for record in db.records]]
    with TestClient(app_for(referrals, db=db, owner=owner)) as client:
        response = client.get("/referrals/me")
    assert response.status_code == 200
    assert response.json() == {
        "referral_code": code or "", "total_invited": 5, "total_paid": 3,
        "total_valid": 2, "total_pending_validation": 1, "discount_percent": 10,
        "referred_by_email": "referrer@example.invalid" if has_referrer else None,
        "milestone": None,
        "milestone_50_progress": 2, "milestone_100_progress": 2,
    }
    for helper in helpers:
        helper.assert_not_awaited()
    assert before == [vars(owner).copy(), *[vars(record).copy() for record in db.records]]
    db.assert_read_only()
    assert db.gets == (1 if has_referrer else 0)
    assert db.no_autoflush_entries >= (2 if has_referrer else 1)
    assert db.autoflush_suppressed is False


def test_false_referral_stats_preserve_all_legacy_helpers(monkeypatch):
    monkeypatch.setitem(settings.__dict__, FLAG, False)
    ensure, revalidate, milestones = patch_referral_helpers(monkeypatch, held=False)
    owner, db = referral_fixture(None)
    with TestClient(app_for(referrals, db=db, owner=owner)) as client:
        response = client.get("/referrals/me")
    assert response.status_code == 200
    assert response.json()["referral_code"] == "LEGACY"
    assert response.json()["total_valid"] == 3
    assert response.json()["discount_percent"] == 15
    ensure.assert_awaited_once_with(owner, db)
    revalidate.assert_awaited_once_with(owner.id, db)
    milestones.assert_awaited_once_with(owner, 3, db)
    db.assert_read_only()


def test_guard_inventory_and_account_closure_exclusions():
    from services.shared_workflow_guard import require_shared_workflow_write

    expected = {
        deals: {("POST", "/deals"), ("PATCH", "/deals/{deal_id}/status"),
                ("POST", "/deals/{deal_id}/sign")},
        success_stories: {("POST", "/success-stories")},
        supplier_reviews: {("POST", "/supplier-reviews")},
    }
    for module, writes in expected.items():
        actual = set()
        for route in module.router.routes:
            if not isinstance(route, APIRoute):
                continue
            mutations = route.methods & {"POST", "PUT", "PATCH", "DELETE"}
            actual.update((method, route.path) for method in mutations)
            calls = [dependency.call for dependency in route.dependant.dependencies]
            if mutations:
                assert require_shared_workflow_write in calls
            else:
                assert require_shared_workflow_write not in calls
        assert actual == writes
    checks = {("POST", "/referrals/mark-paid/{invitee_id}"): True,
              ("POST", "/account/close"): False, ("DELETE", "/account"): False}
    seen = set()
    for route in referrals.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for key, held in checks.items():
            if route.path == key[1] and key[0] in route.methods:
                calls = [dependency.call for dependency in route.dependant.dependencies]
                assert (require_shared_workflow_write in calls) is held
                seen.add(key)
    assert seen == set(checks)


def test_config_default_and_fail_closed_guard_contract(monkeypatch):
    from services import shared_workflow_guard as guard

    assert Settings.model_fields[FLAG].default is True
    for flag in (True, None, 0, "false", False):
        monkeypatch.setattr(guard, "settings", SimpleNamespace(**{FLAG: flag}))
        assert guard.shared_workflow_writes_held() is (flag is not False)
        if flag is False:
            assert guard.require_shared_workflow_write() is None
        else:
            with pytest.raises(HTTPException) as exc:
                guard.require_shared_workflow_write()
            assert exc.value.status_code == 403
            assert exc.value.detail == DETAIL
    monkeypatch.setattr(guard, "settings", SimpleNamespace())
    assert guard.shared_workflow_writes_held() is True
