"""
Decisions apply endpoint (Slice D) tests.

Thin wrapper: the handler is called directly (DI bypassed) with a spied
apply_decision. Verifies passthrough, response mapping, route registration, and
that the request surface is apply-only (no measure/token/entity over HTTP).
"""
import asyncio
import ast
import inspect

from routers import decisions
from routers.decisions import ApplyDecisionRequest, ApplyDecisionResponse, apply_decision_endpoint
from services.decision_apply import DecisionApplyResult


def _run(c):
    return asyncio.run(c)


class _User:
    id = "user-1"


class _Spy:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


def test_handler_passes_through(monkeypatch):
    spy = _Spy(DecisionApplyResult(ok=True, decision_id="d1", execution_log_id="log-1",
                                   status="success", reason=None, decision_outcome_id=None))
    monkeypatch.setattr(decisions.decision_apply, "apply_decision", spy)

    body = ApplyDecisionRequest(overrides={"offer_id": "1", "price": 100},
                                mode="manual_l3", connection_id="c1",
                                idempotency_key="k1", dry_run=False)
    resp = _run(apply_decision_endpoint(decision_id="d1", body=body, current_user=_User(), db="DB"))

    assert isinstance(resp, ApplyDecisionResponse)
    assert resp.ok and resp.execution_log_id == "log-1" and resp.status == "success"
    kw = spy.calls[0]
    assert kw["user_id"] == "user-1"
    assert kw["decision_id"] == "d1"
    assert kw["overrides"] == {"offer_id": "1", "price": 100}
    assert kw["mode"] == "manual_l3"
    assert kw["connection_id"] == "c1"
    assert kw["idempotency_key"] == "k1"
    assert kw["dry_run"] is False
    # apply-only: endpoint never forwards measurement context
    assert "measure" not in kw and "token" not in kw and "entity_id" not in kw


def test_failure_passthrough(monkeypatch):
    spy = _Spy(DecisionApplyResult(ok=False, decision_id="d1", execution_log_id=None,
                                   status="not_applied", reason="missing_overrides"))
    monkeypatch.setattr(decisions.decision_apply, "apply_decision", spy)
    body = ApplyDecisionRequest(overrides={"x": 1})
    resp = _run(apply_decision_endpoint(decision_id="d1", body=body, current_user=_User(), db="DB"))
    assert not resp.ok and resp.reason == "missing_overrides" and resp.status == "not_applied"


def test_route_registered():
    paths = {getattr(r, "path", None) for r in decisions.router.routes}
    assert "/decisions/{decision_id}/apply" in paths


def test_request_surface_is_apply_only():
    fields = set(ApplyDecisionRequest.model_fields)
    assert fields == {"overrides", "mode", "connection_id", "idempotency_key", "dry_run"}
    # never accept a marketplace token or measurement context over HTTP
    for forbidden in ("token", "measure", "entity_id", "window_days"):
        assert forbidden not in fields


def test_no_attribution_learning_or_close_imports():
    tree = ast.parse(inspect.getsource(decisions))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    joined = " ".join(names)
    for forbidden in ("attribution", "learning", "decision_validation", "operator_decision",
                      "wb_client", "ozon_client"):
        assert forbidden not in joined
