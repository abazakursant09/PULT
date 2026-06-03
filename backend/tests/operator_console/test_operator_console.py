"""Constitutional tests for the Operator Console (read-only product layer).

Categories: deterministic rendering, replay reconstruction, fail-closed, route
stability, SVG determinism, immutable topology rendering, no forbidden imports,
no mutation-authority leakage, governance separation, runtime-application
invariance, pressure visualization consistency, replay timeline stability.

Read-only over the REAL substrate (Runtime Application v1). No fabricated layers.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
if str(_FRONTEND) not in sys.path:
    sys.path.insert(0, str(_FRONTEND))

import operator_console as oc
from operator_console.runtime_console_server import app
from fastapi.testclient import TestClient
import runtime_application as ra
import runtime_envelope as renv
import replay_chain as rc

# Console states: the default log + each replay fixture log.
STATES = [("default", oc.DEFAULT_EVENT_LOG)] + [(f.name, f.event_log) for f in rc.ALL_FIXTURES]
SVG_RENDERERS = [
    ("regions", oc.render_regions), ("pressure", oc.render_pressure),
    ("drift", oc.render_drift), ("interventions", oc.render_interventions),
    ("replay", oc.render_replay),
]
ROUTES = ["/runtime/topology", "/runtime/pressure", "/runtime/drift",
          "/runtime/interventions", "/runtime/replay", "/runtime/regions",
          "/runtime/dashboard", "/health"]

client = TestClient(app)


def _state(log):
    return oc.load_state(log)


# ── deterministic rendering ──────────────────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
@pytest.mark.parametrize("rname,fn", SVG_RENDERERS)
def test_svg_render_deterministic(sname, log, rname, fn):
    assert fn(_state(log)) == fn(_state(log))


@pytest.mark.parametrize("sname,log", STATES)
def test_dashboard_render_deterministic(sname, log):
    assert oc.render_dashboard(_state(log)) == oc.render_dashboard(_state(log))


# ── SVG determinism / well-formedness ────────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
@pytest.mark.parametrize("rname,fn", SVG_RENDERERS)
def test_svg_well_formed(sname, log, rname, fn):
    svg = fn(_state(log))
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<svg") == 1 and svg.count("</svg>") == 1


@pytest.mark.parametrize("sname,log", STATES)
@pytest.mark.parametrize("rname,fn", SVG_RENDERERS)
def test_svg_no_random_floats(sname, log, rname, fn):
    # deterministic integer coordinates only — no scientific notation / nan
    svg = fn(_state(log))
    assert "nan" not in svg.lower() and "random" not in svg.lower()


# ── topology determinism + console hash ──────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
def test_topology_deterministic(sname, log):
    a = oc.build_console_topology(_state(log))
    b = oc.build_console_topology(_state(log))
    assert a == b
    assert len(a["operator_console_hash"]) == 64


@pytest.mark.parametrize("sname,log", STATES)
def test_console_hash_matches_application(sname, log):
    topo = oc.build_console_topology(_state(log))
    assert topo["runtime_application_hash"] == ra.build_runtime_application(log).runtime_application_hash


def test_distinct_states_distinct_console_hashes():
    hs = {oc.build_console_topology(_state(log))["operator_console_hash"] for _, log in STATES}
    assert len(hs) == len(STATES)


# ── view-count consistency ───────────────────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
def test_view_counts_consistent(sname, log):
    app_obj = ra.build_runtime_application(log)
    vc = oc.build_console_topology(_state(log))["view_counts"]
    assert vc["pressure"] == app_obj.pressure_region_count
    assert vc["drift"] == app_obj.drift_region_count
    assert vc["intervention"] == app_obj.intervention_surface_count
    assert vc["regions"] == app_obj.pressure_region_count
    assert vc["dashboard"] == 1


# ── pressure visualization consistency ───────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
def test_pressure_render_covers_all_regions(sname, log):
    st = _state(log)
    svg = oc.render_pressure(st)
    for region in st.application.pressure["accumulation_regions"]:
        assert region in svg


# ── replay timeline stability ────────────────────────────────────────────────────

@pytest.mark.parametrize("sname,log", STATES)
def test_replay_timeline_covers_all_ordinals(sname, log):
    st = _state(log)
    svg = oc.render_replay(st)
    for ev in st.application.replay["timeline"]:
        assert f'#{ev["ordinal"]}' in svg


@pytest.mark.parametrize("sname,log", STATES)
def test_replay_identity_in_render(sname, log):
    st = _state(log)
    assert st.application.runtime_application_hash[:16] in oc.render_replay(st)


# ── route stability ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ROUTES)
def test_route_status_ok(path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ROUTES)
def test_route_deterministic(path):
    assert client.get(path).content == client.get(path).content


@pytest.mark.parametrize("path", ROUTES)
def test_route_content_type(path):
    ct = client.get(path).headers["content-type"]
    if path == "/runtime/dashboard":
        assert "text/html" in ct
    elif path in ("/runtime/topology", "/health"):
        assert "application/json" in ct
    else:
        assert "image/svg+xml" in ct


# ── replay reconstruction (route == direct render) ──────────────────────────────

def test_route_matches_direct_render():
    st = oc.default_state()
    assert client.get("/runtime/regions").text == oc.render_regions(st)
    assert client.get("/runtime/pressure").text == oc.render_pressure(st)
    assert client.get("/runtime/drift").text == oc.render_drift(st)
    assert client.get("/runtime/interventions").text == oc.render_interventions(st)
    assert client.get("/runtime/replay").text == oc.render_replay(st)
    assert client.get("/runtime/dashboard").text == oc.render_dashboard(st)


# ── fail-closed behavior ─────────────────────────────────────────────────────────

_BAD_LOGS = [
    [{"event_type": "x", "entity": "e", "wall_clock_time": 1}],
    [{"event_type": "x"}],
    [{"entity": "e"}],
    [{"event_type": "x", "entity": "e", "extra": 1}],
    [{"event_type": "x", "entity": "e", "weight": "5"}],
    ["not-a-dict"],
]


@pytest.mark.parametrize("bad", _BAD_LOGS)
def test_fail_closed_on_bad_log(bad):
    with pytest.raises(ra.BoundaryViolation):
        oc.load_state(bad)


# ── no mutation-authority leakage ────────────────────────────────────────────────

def test_only_read_methods_exposed():
    mutating = set()
    for route in app.routes:
        for m in getattr(route, "methods", set()) or set():
            if m in {"POST", "PUT", "DELETE", "PATCH"}:
                mutating.add(f"{m} {getattr(route, 'path', '?')}")
    assert not mutating, mutating


@pytest.mark.parametrize("sname,log", STATES)
def test_topology_declares_no_authority(sname, log):
    topo = oc.build_console_topology(_state(log))
    assert topo["descriptive_only"] is True
    assert topo["execution_authority"] is False
    assert topo["mutation_authority"] is False


@pytest.mark.parametrize("sname,log", STATES)
def test_console_state_is_frozen(sname, log):
    st = _state(log)
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.view = {}  # type: ignore[misc]


# ── governance separation / runtime-application invariance ──────────────────────

_ENVELOPE_GOLDEN = "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


def test_rendering_does_not_change_envelope_golden():
    for _, log in STATES:
        oc.render_dashboard(_state(log))
    after = renv.build_runtime_envelope(
        collector_signature="COLLECTOR_SIG", signal_set_signature="SIGNALSET_SIG",
        cognition_v2_runtime_hash="COGNITION_HASH", baseline_anchor="47beea1df0c1",
    ).runtime_envelope_hash
    assert after == _ENVELOPE_GOLDEN


@pytest.mark.parametrize("name", [f.name for f in rc.ALL_FIXTURES])
def test_replay_chain_substrate_unaffected(name):
    f = rc.FIXTURES_BY_NAME[name]
    h1 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    oc.build_console_topology(_state(f.event_log))
    h2 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    assert h1 == h2


@pytest.mark.parametrize("sname,log", STATES)
def test_runtime_application_hash_invariant_under_render(sname, log):
    before = ra.build_runtime_application(log).runtime_application_hash
    oc.render_dashboard(_state(log))
    after = ra.build_runtime_application(log).runtime_application_hash
    assert before == after


# ── no forbidden imports / vocabulary in console package ────────────────────────

_PKG = Path(oc.__file__).resolve().parent
_FORBIDDEN_IMPORTS = (
    "import threading", "import multiprocessing", "import asyncio", "import socket",
    "import subprocess", "import requests", "websocket", "from threading",
    "from multiprocessing", "from asyncio",
)
_FORBIDDEN_VOCAB = (
    "intelligent", "autonomous", "agentic", "self-modifying", "self-improving",
    "optimize", "optimizer", "prediction", "predictive", "learning",
    "adaptive execution", "automatic correction", "rebalance",
    "mutation semantics", "runtime mutation", "substrate extension",
    "epoch_11", "meta_runtime",
)


def test_no_forbidden_imports():
    offenders = []
    for src in _PKG.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in _FORBIDDEN_IMPORTS:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


def test_no_forbidden_vocabulary():
    offenders = []
    for src in _PKG.glob("*.py"):
        low = src.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_VOCAB:
            if tok in low:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


# ── cross-process determinism of operator_console_hash ──────────────────────────

def test_cross_process_console_hash_identity():
    expected = oc.build_console_topology(oc.default_state())["operator_console_hash"]
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import operator_console as oc;"
        "print(oc.build_console_topology(oc.default_state())['operator_console_hash'])"
        % str(_FRONTEND)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected
