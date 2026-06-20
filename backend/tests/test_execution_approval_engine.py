"""
Execution approval engine (Slice 10: human-in-loop gate) — tests.

Maps every execution-plan item to a pending approval request with a
confidence-derived risk level. No filtering, no execution, no writes.
"""
import ast
import asyncio
import inspect

from services import execution_approval_engine as appr


def _run(c):
    return asyncio.run(c)


def _item(action_type, conf, priority="high"):
    return {"action_type": action_type, "target": action_type,
            "priority": priority, "confidence": conf, "reason": "r"}


# ── mapping + no filtering ───────────────────────────────────────────────────

def test_all_items_become_pending_no_filtering():
    plan = {"execution_plan": [_item("set_price", 0.9), _item("update_card", 0.4)]}
    q = _run(appr.create_approval_queue(None, "u1", plan))
    assert len(q) == 2                       # nothing filtered
    assert all(r["status"] == "pending" for r in q)
    assert [r["action_type"] for r in q] == ["set_price", "update_card"]
    # required fields present
    for r in q:
        assert set(r.keys()) == {"action_type", "target", "priority",
                                 "confidence", "risk_level", "status"}


def test_order_preserved():
    plan = {"execution_plan": [_item("a", 0.9), _item("b", 0.9), _item("c", 0.9)]}
    q = _run(appr.create_approval_queue(None, "u1", plan))
    assert [r["action_type"] for r in q] == ["a", "b", "c"]


# ── risk model ───────────────────────────────────────────────────────────────

def test_risk_levels():
    plan = {"execution_plan": [
        _item("high_conf", 0.9),    # >0.8 → low
        _item("edge_high", 0.81),   # >0.8 → low
        _item("at_080", 0.8),       # 0.5..0.8 → medium
        _item("mid", 0.65),         # medium
        _item("at_050", 0.5),       # medium
        _item("low_conf", 0.49),    # <0.5 → high
        _item("zero", 0.0),         # high
    ]}
    q = {r["action_type"]: r["risk_level"] for r in _run(appr.create_approval_queue(None, "u", plan))}
    assert q["high_conf"] == "low"
    assert q["edge_high"] == "low"
    assert q["at_080"] == "medium"
    assert q["mid"] == "medium"
    assert q["at_050"] == "medium"
    assert q["low_conf"] == "high"
    assert q["zero"] == "high"


def test_none_confidence_is_high_risk():
    plan = {"execution_plan": [{"action_type": "x", "target": "x",
                                "priority": "low", "confidence": None}]}
    q = _run(appr.create_approval_queue(None, "u", plan))
    assert q[0]["risk_level"] == "high"


# ── input shapes ─────────────────────────────────────────────────────────────

def test_accepts_bare_list():
    q = _run(appr.create_approval_queue(None, "u", [_item("set_price", 0.9)]))
    assert len(q) == 1 and q[0]["action_type"] == "set_price"


def test_empty_plan_empty_queue():
    assert _run(appr.create_approval_queue(None, "u", {"execution_plan": []})) == []
    assert _run(appr.create_approval_queue(None, "u", [])) == []
    assert _run(appr.create_approval_queue(None, "u", {})) == []


# ── determinism ──────────────────────────────────────────────────────────────

def test_deterministic():
    plan = {"execution_plan": [_item("set_price", 0.9), _item("update_card", 0.6)]}
    a = _run(appr.create_approval_queue(None, "u", plan))
    b = _run(appr.create_approval_queue(None, "u", plan))
    assert a == b


def test_input_not_mutated():
    plan = {"execution_plan": [_item("set_price", 0.9)]}
    _run(appr.create_approval_queue(None, "u", plan))
    assert "risk_level" not in plan["execution_plan"][0]
    assert "status" not in plan["execution_plan"][0]


# ── no execution / no ML guards ──────────────────────────────────────────────

def test_no_forbidden_imports():
    src = inspect.getsource(appr)
    for forbidden in ("db.add", "db.commit", "db.flush", ".delete(", "session.add"):
        assert forbidden not in src
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "scheduler", "sklearn", "numpy", "torch",
                "insight_decision_bridge", "decision_apply", "close_measurement",
                "execution_measurement_bridge"):
        assert all(bad not in m for m in mods), f"approval engine must not import {bad}"
