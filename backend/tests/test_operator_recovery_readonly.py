"""SECURITY-2D-1C-C3A — read-only operator recovery contour: config, perimeter, projection, guards.

Unit / SQLite level. The real-PostgreSQL data-scope, pagination, no-mutation and migration proofs live in
test_operator_recovery_pg.py (postgres-explain CI). Everything here is offline: no provider, no executor,
no DB writes to execution_logs, no writer to execution_recovery_audit.
"""
import ast
import pathlib
from types import SimpleNamespace

import pytest

from services.marketplace.operation_fingerprint import compute_fingerprint
from services.marketplace.recovery import operator_view

_BE = pathlib.Path(__file__).resolve().parents[1]
_VALID_KEY = "v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _row(**over):
    """A default SAFE-PENDING set_price row whose stored fingerprint matches its payload."""
    base = dict(
        id="L1", user_id="u1", connection_id="c1", marketplace="wb",
        action_type="set_price", mode="manual_l3",
        payload={"offer_id": "OFF-123", "price": 100},
        status="pending", dispatch_started_at=None,
        idempotency_key=_VALID_KEY, reverted_from=None,
        reconciliation_status=None, reconciliation_attempts=0,
        last_reconciled_at=None, next_reconcile_at=None,
        attempt_count=0, last_attempt_at=None, reown_count=0, last_reowned_at=None,
        manual_resolution=None, resolved_by=None, resolved_at=None, created_at=None,
    )
    base.update(over)
    r = SimpleNamespace(**base)
    if "request_fingerprint" not in over:
        r.request_fingerprint = compute_fingerprint(
            r.user_id, r.connection_id, r.marketplace, r.action_type, r.mode, r.payload, r.reverted_from)
    else:
        r.request_fingerprint = over["request_fingerprint"]
    return r


# ── config defaults ───────────────────────────────────────────────────────────
def test_config_defaults_off_and_empty():
    from config import Settings
    s = Settings()
    assert s.recovery_operator_enabled is False
    assert s.recovery_operator_api_key == ""
    assert s.recovery_operator_id == ""
    assert s.recovery_operator_user_id == ""


# ── perimeter (no DB is opened by the dependency) ─────────────────────────────
def _call_require(monkeypatch, *, enabled, key, oid, uid, header):
    from config import settings
    from routers import internal_recovery
    monkeypatch.setattr(settings, "recovery_operator_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_operator_api_key", key)
    monkeypatch.setattr(settings, "recovery_operator_id", oid)
    monkeypatch.setattr(settings, "recovery_operator_user_id", uid)
    return internal_recovery._require_operator(x_internal_key=header)


def test_flag_off_is_neutral_404(monkeypatch):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _call_require(monkeypatch, enabled=False, key="K", oid="op", uid="u1", header="K")
    assert ei.value.status_code == 404


def test_missing_or_wrong_key_is_403(monkeypatch):
    from fastapi import HTTPException
    for header in (None, "", "WRONG"):
        with pytest.raises(HTTPException) as ei:
            _call_require(monkeypatch, enabled=True, key="RIGHT", oid="op", uid="u1", header=header)
        assert ei.value.status_code == 403


def test_unset_perimeter_settings_fail_closed(monkeypatch):
    from fastapi import HTTPException
    for key, oid, uid in (("", "op", "u1"), ("K", "", "u1"), ("K", "op", "")):
        with pytest.raises(HTTPException) as ei:
            _call_require(monkeypatch, enabled=True, key=key, oid=oid, uid=uid, header=key or "x")
        assert ei.value.status_code == 403


def test_correct_key_yields_server_side_actor_and_tenant(monkeypatch):
    ctx = _call_require(monkeypatch, enabled=True, key="RIGHT", oid="operator-7", uid="tenant-9",
                        header="RIGHT")
    assert ctx.operator_id == "operator-7" and ctx.user_id == "tenant-9"


# ── supported_for_retry matrix ────────────────────────────────────────────────
def test_supported_true_for_safe_pending():
    ok, reason = operator_view.evaluate_retry(_row())
    assert ok is True and reason == "eligibility_safe_pending_preliminary"


@pytest.mark.parametrize("over,expect_reason", [
    (dict(status="in_flight"), "eligibility_not_pending"),
    (dict(status="ambiguous"), "eligibility_not_pending"),
    (dict(status="success"), "eligibility_not_pending"),
    (dict(status="failed"), "eligibility_not_pending"),
    (dict(status="rejected"), "eligibility_not_pending"),
    (dict(status="reverted"), "eligibility_not_pending"),
    (dict(dispatch_started_at=__import__("datetime").datetime.now()), "eligibility_already_dispatched"),
    (dict(reconciliation_status="target_not_observed"), "eligibility_under_reconciliation"),
    (dict(reconciliation_status="still_unknown"), "eligibility_under_reconciliation"),
    (dict(reconciliation_status="manual_attention"), "eligibility_under_reconciliation"),
    (dict(action_type="stop_auto_promotion"), "eligibility_unsupported_action"),
    (dict(action_type="totally_unknown"), "eligibility_unsupported_action"),
    (dict(idempotency_key="review:legacy-nonuuid"), "eligibility_invalid_key"),
    (dict(idempotency_key=None), "eligibility_invalid_key"),
    (dict(manual_resolution="manual_closed"), "eligibility_manually_resolved"),
    (dict(reown_count=5), "eligibility_limit_exceeded"),
    (dict(attempt_count=5), "eligibility_limit_exceeded"),
    (dict(payload={"price": 100}), "eligibility_incomplete_payload"),  # no offer_id
])
def test_supported_false_matrix(over, expect_reason):
    ok, reason = operator_view.evaluate_retry(_row(**over))
    assert ok is False and reason == expect_reason


def test_supported_false_on_fingerprint_mismatch():
    # a valid-shaped but WRONG fingerprint (payload was tampered relative to the stored fp)
    r = _row(request_fingerprint="fp1:" + "b" * 64)
    ok, reason = operator_view.evaluate_retry(r)
    assert ok is False and reason == "eligibility_fingerprint_mismatch"


def test_supported_false_on_malformed_fingerprint():
    r = _row(request_fingerprint="not-a-fingerprint")
    ok, reason = operator_view.evaluate_retry(r)
    assert ok is False and reason == "eligibility_invalid_fingerprint"


# ── target_reference: stable, non-reversible, operator-key-independent ─────────
def test_target_reference_shape_and_no_raw_leak():
    r = _row()
    ref = operator_view.target_reference(r)
    assert ref and ref.startswith("tgt:") and len(ref) == 4 + 12
    assert "OFF-123" not in ref                      # raw provider id never leaks into the reference


def test_target_reference_stable_across_operator_key_rotation(monkeypatch):
    from config import settings
    r = _row()
    monkeypatch.setattr(settings, "recovery_operator_api_key", "KEY-A")
    a = operator_view.target_reference(r)
    monkeypatch.setattr(settings, "recovery_operator_api_key", "KEY-B-rotated")
    b = operator_view.target_reference(r)
    assert a == b                                    # keyed by secret_key, not the operator key


def test_target_reference_differs_by_target():
    assert operator_view.target_reference(_row(payload={"offer_id": "A", "price": 1})) != \
           operator_view.target_reference(_row(payload={"offer_id": "B", "price": 1}))


def test_target_reference_none_for_unsupported_or_incomplete():
    assert operator_view.target_reference(_row(action_type="stop_auto_promotion")) is None
    assert operator_view.target_reference(_row(payload={"price": 1})) is None  # no offer_id


# ── strict allowlist response schema ──────────────────────────────────────────
def test_response_schema_is_exact_allowlist():
    from routers.internal_recovery import OperatorOperationView
    allowed = {
        "log_id", "marketplace", "action_type", "mode", "status", "manual_resolution",
        "reconciliation", "created_at", "dispatch_started_at", "last_attempt_at", "last_reowned_at",
        "attempt_count", "reown_count", "reverted", "supported_for_retry", "reason_code",
        "target_reference",
    }
    assert set(OperatorOperationView.model_fields.keys()) == allowed
    forbidden = {"user_id", "connection_id", "idempotency_key", "request_fingerprint", "payload",
                 "api_request_id", "result", "error_code"}
    assert not (forbidden & set(OperatorOperationView.model_fields.keys()))


def test_projected_dump_contains_no_forbidden_values():
    from routers.internal_recovery import _project
    r = _row(id="LOG-1")
    view = _project(r)
    dumped = view.model_dump()
    blob = repr(dumped)
    # raw target id, the operation key and the fingerprint must never appear in the projection
    assert "OFF-123" not in blob
    assert _VALID_KEY not in blob
    assert r.request_fingerprint not in blob
    assert set(dumped.keys()) - {"reconciliation"} | {"reconciliation"}  # sanity: dict built


# ── AST/import guards: read-only, no executor/provider/dispatch/writer ────────
def _src(*rel):
    return (_BE.joinpath(*rel)).read_text(encoding="utf-8")


def _imported(src):
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def test_router_and_view_do_not_import_executor_or_providers():
    banned = ("executor", "wb_client", "ozon_client", "yandex", "ozon_performance", "action_catalog",
              "credential_vault", "reviews", "reown_sweep", "recovery_sweep", "reconcile_read")
    for src in (_src("routers", "internal_recovery.py"),
                _src("services", "marketplace", "recovery", "operator_view.py")):
        mods = _imported(src)
        for m in mods:
            assert not any(b in m for b in banned), f"read-only contour must not import {m}"


def test_no_execution_log_mutation_or_audit_writer():
    for src in (_src("routers", "internal_recovery.py"),
                _src("services", "marketplace", "recovery", "operator_view.py")):
        low = src.lower()
        # no SQL writes at all in this contour
        for verb in ("update execution_logs", "insert into execution_logs",
                     "delete from execution_logs", "insert into execution_recovery_audit",
                     "update execution_recovery_audit", ".add(", ".commit(", ".flush("):
            assert verb not in low, f"read-only contour must not contain {verb!r}"


def test_no_dispatch_execute_reown_reconcile_calls():
    # Precise provider/executor/dispatch tokens only — bare `db.execute(...)` (a READ) is allowed.
    for src in (_src("routers", "internal_recovery.py"),
                _src("services", "marketplace", "recovery", "operator_view.py")):
        for name in ("executor.execute", ".execute(db=", "spec.dispatch", ".dispatch(",
                     "publish_feedback_answer", ".revert(", "run_reown_sweep", "run_recovery_sweep",
                     "reconcile_read", "run_one", "_FENCE_CAS", "_REOWN_CAS"):
            assert name not in src, f"read-only contour must not reference {name}"


def test_router_not_added_to_broad_csrf_exemption():
    # C3A GET is not a state method (never CSRF-checked). C3B adds a NARROW POST exemption for exactly the
    # three resolution routes (test_operator_resolve.test_csrf_exemption_is_narrow proves the scope); this
    # guard only forbids a BROAD prefix/exact bypass of the /api/internal/recovery tree.
    from csrf import _EXEMPT_PREFIX, _EXEMPT_EXACT
    assert not any("internal/recovery" in p for p in _EXEMPT_PREFIX)
    assert not any("internal/recovery" in p for p in _EXEMPT_EXACT)


def test_alembic_single_head_is_rop():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rob1a2b3c4d01"]
