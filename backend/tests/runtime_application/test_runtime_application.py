"""Constitutional tests for the Operational Runtime Application Layer v1.

Categories: replay determinism, fail-closed, topology reconstruction, append-only
lineage, drift visibility, intervention visibility, runtime projection stability,
governance separation, frozen substrate invariance, no forbidden vocabulary,
no forbidden imports, no mutation-authority leakage.

Golden runtime_application_hash per fixture is pinned under
tests/runtime_application_fixtures/. Regenerate: RUNTIME_APP_UPDATE=1 pytest ...
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import runtime_application as ra
from runtime_application.operational_state_projection import category_of
import replay_chain as rc
import runtime_envelope as renv

FIXTURES = [(f.name, f.event_log) for f in rc.ALL_FIXTURES]
NAMES = [n for n, _ in FIXTURES]
_FIXDIR = Path(__file__).resolve().parents[1] / "runtime_application_fixtures"


def _freeze(name, log):
    app = ra.build_runtime_application(log)
    return {"name": name, "summary": app.summary(), "console": ra.console_view(ra.ingest(log))}


if os.environ.get("RUNTIME_APP_UPDATE") == "1":
    _FIXDIR.mkdir(parents=True, exist_ok=True)
    for _n, _log in FIXTURES:
        with (_FIXDIR / f"{_n}.json").open("w", encoding="utf-8") as fh:
            json.dump(_freeze(_n, _log), fh, ensure_ascii=False, indent=2, sort_keys=True)


def _load(name):
    with (_FIXDIR / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


# ── replay determinism ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_build_is_deterministic(name, log):
    assert ra.build_runtime_application(log).runtime_application_hash == \
        ra.build_runtime_application(log).runtime_application_hash


@pytest.mark.parametrize("name,log", FIXTURES)
def test_console_view_deterministic(name, log):
    s = ra.ingest(log)
    assert ra.console_view(s) == ra.console_view(s)


@pytest.mark.parametrize("name,log", FIXTURES)
def test_hash_is_64_hex(name, log):
    h = ra.build_runtime_application(log).runtime_application_hash
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_distinct_fixtures_distinct_hashes():
    hs = {ra.build_runtime_application(log).runtime_application_hash for _, log in FIXTURES}
    assert len(hs) == len(FIXTURES)


# ── frozen golden hash ───────────────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("RUNTIME_APP_UPDATE") == "1", reason="regenerating")
@pytest.mark.parametrize("name,log", FIXTURES)
def test_golden_hash_frozen(name, log):
    ref = _load(name)
    assert ra.build_runtime_application(log).summary() == ref["summary"]


@pytest.mark.skipif(os.environ.get("RUNTIME_APP_UPDATE") == "1", reason="regenerating")
@pytest.mark.parametrize("name,log", FIXTURES)
def test_golden_console_frozen(name, log):
    ref = _load(name)
    assert ra.console_view(ra.ingest(log)) == ref["console"]


# ── topology reconstruction ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_reconstruct_from_stream(name, log):
    stream = ra.ingest(log)
    a = ra.build_from_stream(stream)
    b = ra.build_runtime_application(log)
    assert a == b


@pytest.mark.parametrize("name,log", FIXTURES)
def test_attestation_verifies(name, log):
    assert ra.verify_attestation(ra.attest(log)) is True


# ── append-only lineage ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_ingest_order_is_append_index(name, log):
    s = ra.ingest(log)
    assert [e["ordinal"] for e in s.events] == list(range(len(log)))


@pytest.mark.parametrize("name,log", FIXTURES)
def test_append_preserves_prefix(name, log):
    if not log:
        pytest.skip("empty")
    head = ra.ingest(log[:-1])
    full = head.append(dict(log[-1]))
    assert full.events[:-1] == head.events
    assert full.length == head.length + 1


# (fixture, k) prefix lineage — expands coverage across many cut points
_PREFIX_CASES = [(n, log, k) for n, log in FIXTURES for k in range(1, len(log) + 1)]


@pytest.mark.parametrize("name,log,k", _PREFIX_CASES)
def test_prefix_is_stable_under_growth(name, log, k):
    prefix = ra.ingest(log[:k])
    full = ra.ingest(log)
    assert full.events[:k] == prefix.events


# ── drift visibility ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_drift_map_deterministic(name, log):
    s = ra.ingest(log)
    assert ra.drift_map(s) == ra.drift_map(s)


@pytest.mark.parametrize("name,log", FIXTURES)
def test_instability_markers_are_subset_of_regions(name, log):
    s = ra.ingest(log)
    dm = ra.drift_map(s)
    assert set(dm["instability_markers"]).issubset(set(dm["drift_regions"]))


# ── intervention visibility ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_intervention_surfaces_observation_only(name, log):
    for surf in ra.intervention_surfaces(ra.ingest(log)):
        assert surf["observation_only"] is True
        assert surf["accumulated_weight"] >= ra.INTERVENTION_VISIBILITY_THRESHOLD


@pytest.mark.parametrize("name,log", FIXTURES)
def test_intervention_surfaces_sorted(name, log):
    surfs = ra.intervention_surfaces(ra.ingest(log))
    keys = [(-s["accumulated_weight"], s["region"]) for s in surfs]
    assert keys == sorted(keys)


# ── runtime projection stability + pressure consistency ──────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_pressure_accumulation_matches_weights(name, log):
    s = ra.ingest(log)
    expected = {}
    for ev in s.events:
        expected[category_of(ev["entity"])] = expected.get(category_of(ev["entity"]), 0) + ev["weight"]
    assert ra.accumulation_regions(s) == {k: expected[k] for k in sorted(expected)}


@pytest.mark.parametrize("name,log", FIXTURES)
def test_state_event_count_matches(name, log):
    assert ra.project_state(ra.ingest(log))["event_count"] == len(log)


# ── governance separation / frozen substrate invariance ──────────────────────────

_ENVELOPE_GOLDEN = "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


def test_building_app_does_not_change_envelope_golden():
    before = renv.build_runtime_envelope(
        collector_signature="COLLECTOR_SIG", signal_set_signature="SIGNALSET_SIG",
        cognition_v2_runtime_hash="COGNITION_HASH", baseline_anchor="47beea1df0c1",
    ).runtime_envelope_hash
    for _, log in FIXTURES:
        ra.build_runtime_application(log)
    after = renv.build_runtime_envelope(
        collector_signature="COLLECTOR_SIG", signal_set_signature="SIGNALSET_SIG",
        cognition_v2_runtime_hash="COGNITION_HASH", baseline_anchor="47beea1df0c1",
    ).runtime_envelope_hash
    assert before == after == _ENVELOPE_GOLDEN


@pytest.mark.parametrize("name,log", FIXTURES)
def test_replay_chain_substrate_unaffected(name, log):
    f = rc.FIXTURES_BY_NAME[name]
    h1 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    ra.build_runtime_application(log)  # building app must not perturb substrate
    h2 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    assert h1 == h2


# ── no mutation-authority leakage ────────────────────────────────────────────────

def test_boundary_flags_descriptive_only():
    assert ra.EXECUTION_AUTHORITY is False
    assert ra.MUTATION_AUTHORITY is False
    assert ra.DESCRIPTIVE_ONLY is True
    assert ra.FAIL_CLOSED is True
    assert ra.REPLAY_COMPATIBLE is True
    assert ra.DETERMINISTIC is True


@pytest.mark.parametrize("name,log", FIXTURES)
def test_application_is_frozen_immutable(name, log):
    app = ra.build_runtime_application(log)
    with pytest.raises(dataclasses.FrozenInstanceError):
        app.runtime_application_hash = "x"  # type: ignore[misc]


@pytest.mark.parametrize("name,log", FIXTURES)
def test_stream_is_frozen_immutable(name, log):
    s = ra.ingest(log)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.events = ()  # type: ignore[misc]


# ── fail-closed behavior ─────────────────────────────────────────────────────────

_BAD_EVENTS = [
    {"event_type": "x", "entity": "e", "wall_clock_time": 1},   # non-deterministic key
    {"event_type": "x", "entity": "e", "process_id": 7},        # non-deterministic key
    {"event_type": "x"},                                         # missing entity
    {"entity": "e"},                                             # missing event_type
    {"event_type": "x", "entity": "e", "extra": 1},             # unsupported field
    {"event_type": "x", "entity": "e", "weight": "5"},          # bad weight type
    {"event_type": "x", "entity": "e", "weight": True},         # bool weight refused
    "not-a-dict",                                                # non-dict
]


@pytest.mark.parametrize("bad", _BAD_EVENTS)
def test_fail_closed_rejects_bad_event(bad):
    with pytest.raises(ra.BoundaryViolation):
        ra.ingest([bad])


# ── tamper detection ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_tamper_event_fails(name, log):
    if not log:
        pytest.skip("empty")
    att = ra.attest(log)
    bad = (dict(att.raw_events[0], weight=99999),) + att.raw_events[1:]
    assert ra.verify_attestation(dataclasses.replace(att, raw_events=bad)) is False


@pytest.mark.parametrize("name,log", FIXTURES)
def test_tamper_hash_fails(name, log):
    att = ra.attest(log)
    bad_app = dataclasses.replace(att.application, runtime_application_hash="0" * 64)
    assert ra.verify_attestation(dataclasses.replace(att, application=bad_app)) is False


# ── cross-process determinism ────────────────────────────────────────────────────

def test_cross_process_identity():
    f = rc.FIXTURES_BY_NAME["replay_instability_burst"]
    expected = ra.build_runtime_application(f.event_log).runtime_application_hash
    code = (
        "import runtime_application as ra, replay_chain as rc;"
        "f=rc.FIXTURES_BY_NAME['replay_instability_burst'];"
        "print(ra.build_runtime_application(f.event_log).runtime_application_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


# ── no forbidden vocabulary / imports ────────────────────────────────────────────

_PKG = Path(ra.__file__).resolve().parent
_FORBIDDEN_VOCAB = (
    "intelligent", "autonomous", "agentic", "self-modifying", "self_modifying",
    "self-improving", "predictive", "prediction", "optimizer", "optimize",
    "learning", "adaptive execution", "automatic correction", "rebalance",
    "mutation semantics", "runtime mutation", "substrate extension",
    "orchestration mutation", "epoch_11", "meta_runtime",
)
_FORBIDDEN_IMPORTS = (
    "import threading", "import asyncio", "import multiprocessing", "import socket",
    "import requests", "import subprocess", "import inspect", "import importlib",
    "import pkgutil", "from threading", "from asyncio", "from multiprocessing",
)


def test_no_forbidden_vocabulary():
    offenders = []
    for src in _PKG.glob("*.py"):
        low = src.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_VOCAB:
            if tok in low:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


def test_no_forbidden_imports():
    offenders = []
    for src in _PKG.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in _FORBIDDEN_IMPORTS:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders
