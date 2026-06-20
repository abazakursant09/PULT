"""
Insight → Decision bridge (Slice 2: decision_id execution provenance) — tests.

/execute captures PromoteResult.decision_id and threads it into the existing
executor.execute calls (provenance only). No new execution behavior, no
measurement, no apply_decision. Promotion stays non-blocking: a blocked or
raising promotion leaves decision_id=None and execution proceeds.
"""
import asyncio
import inspect
import types

from routers import action_engine as ae
from services.insight_decision_bridge import PromoteResult


def _run(c):
    return asyncio.run(c)


class _Res:
    ok = True
    status = "success"
    log_id = "l1"
    error = None
    marketplace = "wb"


def _single_plan():
    return ae._imap.Plan(insight_key="margin_crisis:wb:SKU1", itype="margin_crisis",
                         action_type="set_price", payload={"price": 1})


def _wire_single(monkeypatch, *, promote):
    """Stub resolve_plan (ready single), executor.execute (capture kwargs), promote."""
    captured = []

    async def fake_resolve(db, uid, key, overrides):
        return _single_plan()

    async def fake_exec(**kw):
        captured.append(kw)
        return _Res()

    monkeypatch.setattr(ae._imap, "resolve_plan", fake_resolve)
    monkeypatch.setattr(ae._executor, "execute", fake_exec)
    monkeypatch.setattr(ae, "_promote_decision", promote)
    return captured


def _body():
    return types.SimpleNamespace(overrides={}, dry_run=False)


def _user():
    return types.SimpleNamespace(id="u1")


# ── single path threads promoted decision_id ─────────────────────────────────

def test_single_path_passes_promoted_decision_id(monkeypatch):
    async def promote(db, *, user_id, insight):
        return PromoteResult("dec-123", created=True, promotable=True, reason=None)

    captured = _wire_single(monkeypatch, promote=promote)
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", _body(),
                                   current_user=_user(), db=None))
    assert len(captured) == 1
    assert captured[0]["decision_id"] == "dec-123"
    assert resp.success is True
    assert resp.action_type == "set_price"


# ── blocked promotion → decision_id None, execution still proceeds ───────────

def test_blocked_promotion_threads_none_and_executes(monkeypatch):
    async def promote(db, *, user_id, insight):
        return PromoteResult(None, created=False, promotable=False, reason="non_promotable_sku")

    captured = _wire_single(monkeypatch, promote=promote)
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", _body(),
                                   current_user=_user(), db=None))
    assert len(captured) == 1
    assert captured[0]["decision_id"] is None
    assert resp.success is True


# ── promotion raises → non-blocking, decision_id None, execution proceeds ────

def test_promotion_exception_is_non_blocking(monkeypatch):
    async def promote(db, *, user_id, insight):
        raise RuntimeError("boom")

    captured = _wire_single(monkeypatch, promote=promote)
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", _body(),
                                   current_user=_user(), db=None))
    assert len(captured) == 1
    assert captured[0]["decision_id"] is None
    assert resp.success is True


# ── batch path threads the SAME decision_id into every executor call ─────────

def test_batch_path_passes_same_decision_id_to_all(monkeypatch):
    async def promote(db, *, user_id, insight):
        return PromoteResult("dec-batch", created=True, promotable=True, reason=None)

    batch_plan = ae._imap.Plan(insight_key="rating_good:wb:SKU1", itype="rating_good",
                               action_type="publish_review_response", payload={}, batch=True)

    async def fake_resolve(db, uid, key, overrides):
        return batch_plan

    captured = []

    async def fake_exec(**kw):
        captured.append(kw)
        return _Res()

    # Two prepared reviews for the batch loop.
    class _Review:
        def __init__(self, rid):
            self.id = rid
            self.external_review_id = f"ext-{rid}"
            self.response_text = "thanks"
            self.rating = 5
            self.status = "pending"
            self.published_at = None
            self.execution_log_id = None

    class _ScalarResult:
        def __init__(self, rows):
            self._rows = rows
        def scalars(self):
            return self
        def all(self):
            return self._rows

    class _DB:
        async def execute(self, *a, **k):
            return _ScalarResult([_Review("r1"), _Review("r2")])
        async def commit(self):
            return None

    monkeypatch.setattr(ae._imap, "resolve_plan", fake_resolve)
    monkeypatch.setattr(ae._executor, "execute", fake_exec)
    monkeypatch.setattr(ae, "_promote_decision", promote)
    monkeypatch.setattr(ae, "_imap_negative_max", lambda: 3)

    resp = _run(ae.execute_insight("rating_good:wb:SKU1", _body(),
                                   current_user=_user(), db=_DB()))
    assert len(captured) == 2
    assert {c["decision_id"] for c in captured} == {"dec-batch"}
    assert resp.success is True


# ── response contract unchanged (fields present, no new fields used) ─────────

def test_response_contract_unchanged(monkeypatch):
    async def promote(db, *, user_id, insight):
        return PromoteResult("dec-x", created=True, promotable=True, reason=None)

    _wire_single(monkeypatch, promote=promote)
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", _body(),
                                   current_user=_user(), db=None))
    # Same fields the pre-Slice-2 single path returned.
    assert resp.success is True
    assert resp.status == "success"
    assert resp.action_type == "set_price"
    assert resp.execution_id == "l1"
    assert not hasattr(resp, "decision_id")  # contract not widened


# ── architecture guards ──────────────────────────────────────────────────────

def test_execute_does_not_call_apply_or_measurement():
    src = inspect.getsource(ae.execute_insight)
    for forbidden in ("apply_decision", "open_measurement", "close_measurement",
                      "decision_validation"):
        assert forbidden not in src, f"/execute must not reference {forbidden}"


def test_decision_id_threaded_into_both_executor_calls():
    src = inspect.getsource(ae.execute_insight)
    # decision_id captured once, passed into every executor.execute call.
    assert src.count("decision_id=decision_id") == 2
    assert "decision_id = pres.decision_id if pres else None" in src
