"""SECURITY-2D-1B-B — the manual-identity contract: header only, body key forbidden, missing → 422.

These assertions fire in the route glue BEFORE the executor is reached, so no DB/connection is needed.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from routers.execution import execute_action
from routers.decisions import apply_decision_endpoint, ApplyDecisionRequest
from routers.decision_apply import decision_apply_confirm, ConfirmRequest
from schemas.marketplace import ExecuteRequest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


_USER = SimpleNamespace(id=str(uuid.uuid4()))
_GOOD = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def test_execute_body_key_forbidden():
    body = ExecuteRequest(action_type="set_price", payload={}, idempotency_key=_GOOD)
    with pytest.raises(HTTPException) as e:
        _run(execute_action(body=body, current_user=_USER, db=None, idempotency_key=None))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "BODY_OPERATION_KEY_FORBIDDEN"


def test_execute_missing_header_required():
    body = ExecuteRequest(action_type="set_price", payload={})
    with pytest.raises(HTTPException) as e:
        _run(execute_action(body=body, current_user=_USER, db=None, idempotency_key=None))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "OPERATION_KEY_REQUIRED"


def test_execute_malformed_header_invalid():
    body = ExecuteRequest(action_type="set_price", payload={})
    with pytest.raises(HTTPException) as e:
        _run(execute_action(body=body, current_user=_USER, db=None, idempotency_key="not-a-uuid"))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "OPERATION_KEY_INVALID"


def test_execute_header_and_body_still_forbidden():
    # both present → still 422 (body key rejected first; no "header wins")
    body = ExecuteRequest(action_type="set_price", payload={}, idempotency_key=_GOOD)
    with pytest.raises(HTTPException) as e:
        _run(execute_action(body=body, current_user=_USER, db=None, idempotency_key=_GOOD))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "BODY_OPERATION_KEY_FORBIDDEN"


def test_decision_apply_body_key_forbidden():
    body = ApplyDecisionRequest(overrides={"price": 1}, idempotency_key=_GOOD)
    with pytest.raises(HTTPException) as e:
        _run(apply_decision_endpoint(decision_id="d1", body=body, current_user=_USER, db=None))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "BODY_OPERATION_KEY_FORBIDDEN"


def test_confirm_body_key_forbidden():
    body = ConfirmRequest(marketplace="wildberries", idempotency_key=_GOOD)
    with pytest.raises(HTTPException) as e:
        _run(decision_apply_confirm(decision_id="d1", body=body, current_user=_USER, db=None))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "BODY_OPERATION_KEY_FORBIDDEN"


def test_confirm_body_key_now_optional():
    # the deprecated field is Optional — a body WITHOUT it constructs fine (no silent requirement)
    ConfirmRequest(marketplace="wildberries")
