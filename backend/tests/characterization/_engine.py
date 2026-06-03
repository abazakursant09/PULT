"""Shared characterization engine (Sprint 71).

Reusable golden-master harness for every logic module. A per-module cases.py
defines build_cases() -> {case_name: output}; a per-module
test_characterization.py calls run_snapshot(__file__, build_cases).

The committed snapshot.json beside each test is the REFERENCE behavior. The test
recomputes live and asserts equality. Divergence -> failure.

Regenerate intentionally with: CHAR_UPDATE=1 python -m pytest <path>

Observe-only: nothing here imports or mutates runtime logic beyond calling it.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest


# ── Deterministic fixtures ──────────────────────────────────────────────────

class Duck:
    """Permissive stand-in for an enriched InsightItem. Any unset attribute
    reads as None (matching the getattr(insight, x, None) access pattern used
    throughout logic/). Explicit kwargs override."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def __getattr__(self, _name: str) -> None:  # only called for missing attrs
        return None

    def __repr__(self) -> str:
        return f"Duck({self.__dict__!r})"


def insight(**kw: Any) -> Duck:
    base = dict(key="margin_crisis:wildberries:SKU1", status="active",
                category="margin_crisis", marketplace="wildberries",
                confidence=70, impact_score=50)
    base.update(kw)
    return Duck(**base)


# ── Serialization ────────────────────────────────────────────────────────────

# Fields holding randomly-generated identifiers (uuid / per-call ids). We freeze
# behavior, not random identifiers, so these are normalized in snapshots.
_VOLATILE_KEYS = frozenset({"id", "scenario_id", "focus_id"})
# Fields built from set() — element order is hash-seed dependent across runs.
# Sorted in snapshots so the freeze is deterministic without changing contents.
_SET_ORDER_KEYS = frozenset({
    "affected_products", "insight_types", "marketplaces",
    "linked_signals", "linked_scenarios", "linked_chains",
})


def jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Duck):
        return {"__duck__": {k: jsonable(v) for k, v in sorted(value.__dict__.items())}}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _VOLATILE_KEYS:
                out[str(k)] = "<volatile>"
            elif k in _SET_ORDER_KEYS and isinstance(v, (list, tuple)):
                out[str(k)] = sorted(jsonable(x) for x in v)
            else:
                out[str(k)] = jsonable(v)
        return out
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)  # last-resort stable-ish; flagged by review if it appears


import inspect as _inspect
from datetime import datetime as _dt

# Curated deterministic value per parameter NAME. Goal: feed each logic function
# inputs it accepts on a neutral/default path so the captured output reflects
# REAL behavior (not a fixture-induced crash). Unknown params default to None.
_FIXED_NOW = _dt(2026, 1, 1, 12, 0, 0)

_LIST_PARAMS = frozenset({
    "insights", "active", "portfolio_patterns", "chains", "scenarios",
    "marketplace_patterns", "operator_decisions", "decisions", "recommendations",
    "recs", "insight_summaries", "sequence", "sequencing",
})
_DICT_PARAMS = frozenset({
    "resolved_history", "notif_counts", "rebuild_outcomes", "meta", "metrics",
})
_OBJ_PARAMS = frozenset({
    "insight", "lifecycle", "trajectory", "decay", "forecast", "operator_profile",
    "profile", "operational_focus", "pattern", "memory", "ev", "outcome",
    "tradeoff", "portfolio_pattern",
})

_CURATED: dict[str, Any] = {
    # identities / categories
    "key": "margin_crisis:wildberries:SKU1", "insight_key": "margin_crisis:wildberries:SKU1",
    "category": "margin_crisis", "insight_category": "margin_crisis",
    "rule_category": "margin_crisis", "insight_type": "margin_crisis",
    "pattern_type": "margin_crisis", "marketplace": "wildberries",
    "action_taken": "applied", "role": "primary", "summary_type": "operational",
    # confidences / scores
    "confidence": 70, "insight_confidence": 70,
    "recovery_probability": 50, "reversal_probability": 50, "resilience_score": 50,
    "absorption_capacity": 50, "pressure_accumulation": 0.5,
    # counts
    "signal_recurrence_count": 1, "notif_count": 1, "recurrence_count": 1,
    "concurrent_active_count": 1, "unresolved_count": 1, "alerts_last_7d": 2,
    "resolved_count_90d": 3, "crisis_recurrence_count": 1, "focus_churn": 1,
    "past_cnt": 1, "max_patterns": 3, "sequence_stage": 1, "upstream_unresolved": 0,
    "causal_depth": 1, "portfolio_complexity": 1, "automation_level": 1,
    # days / windows / ages
    "stabilization_window_days": 14, "counterfactual_transition_window_days": 14,
    "forecast_instability_window_days": 14, "days_active": 10, "age_days": 10,
    "operational_age_days": 90,
    # floats
    "fatigue_score": 0.0, "stability_credit": 0.0, "financial_impact": 100000.0,
    "monthly_rub": 100000.0, "operator_sensitivity": 1.0,
    # bools
    "recurring": False,
    # time
    "now": _FIXED_NOW, "resolved_at": None,
}


def auto_kwargs(fn: Callable, empty: bool = False) -> dict[str, Any]:
    """Build deterministic kwargs for fn from its parameter names."""
    kw: dict[str, Any] = {}
    for name, p in _inspect.signature(fn).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in _LIST_PARAMS:
            kw[name] = [] if empty else [insight(signal_lifecycle_stage="recurring"),
                                          insight(key="seo_opportunity:ozon:S2", category="seo_opportunity")]
        elif name in _DICT_PARAMS:
            kw[name] = {}
        elif name in _OBJ_PARAMS:
            kw[name] = insight()
        elif name in _CURATED:
            kw[name] = _CURATED[name]
        else:
            kw[name] = None  # *_state / band / direction / style etc. -> neutral path
    return kw


def has_list_param(fn: Callable) -> bool:
    return any(n in _LIST_PARAMS for n in _inspect.signature(fn).parameters)


def call(fn: Callable, *args: Any, **kw: Any) -> Any:
    """Call fn and record its observed behavior — value OR raised exception.
    Freezing a raise is valid characterization (current behavior), per Sprint 71
    'if a bug is discovered: document it, freeze it, do not fix it'."""
    try:
        return jsonable(fn(*args, **kw))
    except Exception as exc:  # noqa: BLE001 — characterizing observed behavior
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


# ── Snapshot runner ──────────────────────────────────────────────────────────

def _snapshot_path(test_file: str) -> Path:
    return Path(test_file).resolve().parent / "snapshot.json"


def run_snapshot(test_file: str, build_cases: Callable[[], dict[str, Any]]) -> None:
    snap = _snapshot_path(test_file)
    if os.environ.get("CHAR_UPDATE") == "1":
        snap.parent.mkdir(parents=True, exist_ok=True)
        with snap.open("w", encoding="utf-8") as fh:
            json.dump(build_cases(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        pytest.skip(f"CHAR_UPDATE=1 — snapshot written: {snap.name}")
    assert snap.exists(), f"Missing snapshot {snap}. Generate with CHAR_UPDATE=1."
    with snap.open(encoding="utf-8") as fh:
        reference = json.load(fh)
    live = build_cases()
    assert set(live) == set(reference), (
        f"Case set changed for {snap.parent.name}: "
        f"added={sorted(set(live)-set(reference))} removed={sorted(set(reference)-set(live))}. "
        f"Regenerate with CHAR_UPDATE=1 and review."
    )
    mismatches = [k for k in reference if live[k] != reference[k]]
    assert not mismatches, (
        f"RUNTIME BEHAVIOR CHANGED in {snap.parent.name} for cases {mismatches}.\n"
        + "\n".join(f"  {k}:\n    ref={reference[k]!r}\n    live={live[k]!r}" for k in mismatches)
        + "\nIf intentional: rerun with CHAR_UPDATE=1 and review the snapshot diff."
    )
