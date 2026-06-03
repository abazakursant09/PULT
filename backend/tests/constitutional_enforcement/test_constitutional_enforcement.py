"""Constitutional Enforcement tests (Sprint 78).

Categories: valid constitution, tampered schema/logic/envelope/replay/runtime/
console/review/root, cross-process identity, deterministic reporting, pipeline
verification, forbidden imports, substrate protection.

Pinned goldens. Read-only over the frozen substrate; no layer modified.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import constitutional_enforcement as ce
import root_constitution as root

GOLDEN_ROOT = "26e4462ff725f0282ca4b4d1eb82ad9545aeaa08929bd9895d904f3d5f3afe75"
GOLDEN_SIGNATURE = "f7bf4ad534ff681fefaba32e9c01fb04202646cbf6efef5bd6e256bfd64d328c"
LAYERS = list(ce.ENFORCED_LAYERS)

_LIVE_ROOT = root.build_root_constitution()
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _verify_tampered(**overrides) -> str:
    tampered = dataclasses.replace(_LIVE_ROOT, **overrides)
    return ce._verify(tampered, ce.EXPECTED_ANCHORS, ce.EXPECTED_ROOT)


# ── valid constitution ───────────────────────────────────────────────────────────

def test_verify_full_constitution_valid():
    assert ce.verify_full_constitution() == ce.VALID


@pytest.mark.parametrize("i", list(range(40)))
def test_verify_valid_repeat(i):
    assert ce.verify_full_constitution() == ce.VALID


def test_verify_returns_only_valid_or_invalid():
    assert ce.verify_full_constitution() in (ce.VALID, ce.INVALID)


def test_verify_layers_all_true_on_clean():
    m = ce.verify_layers(_LIVE_ROOT)
    assert all(m.values()) and len(m) == 7


# ── deterministic reporting ──────────────────────────────────────────────────────

def test_report_status_valid():
    assert ce.build_constitution_report().constitutional_status == ce.VALID


def test_report_verified_layers_seven():
    assert len(ce.build_constitution_report().verified_layers) == 7
    assert list(ce.build_constitution_report().verified_layers) == LAYERS


def test_report_root_hash_golden():
    assert ce.build_constitution_report().root_constitutional_hash == GOLDEN_ROOT


def test_report_signature_golden():
    assert ce.build_constitution_report().verification_signature == GOLDEN_SIGNATURE


def test_report_signature_64_hex():
    sig = ce.build_constitution_report().verification_signature
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


@pytest.mark.parametrize("i", list(range(40)))
def test_report_deterministic_repeat(i):
    r = ce.build_constitution_report()
    assert r.verification_signature == GOLDEN_SIGNATURE
    assert r.root_constitutional_hash == GOLDEN_ROOT
    assert r.constitutional_status == ce.VALID


def test_report_is_frozen():
    r = ce.build_constitution_report()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.verification_signature = "x"  # type: ignore[misc]


# ── tampered layers -> INVALID (named per directive category) ───────────────────

def test_tampered_schema():
    assert _verify_tampered(schema_baseline_revision="tampered") == ce.INVALID


def test_tampered_logic():
    assert _verify_tampered(logic_characterization_hash="0" * 64) == ce.INVALID


def test_tampered_envelope():
    assert _verify_tampered(runtime_envelope_hash="0" * 64) == ce.INVALID


def test_tampered_replay():
    assert _verify_tampered(replay_chain_hash="0" * 64) == ce.INVALID


def test_tampered_runtime():
    assert _verify_tampered(runtime_application_hash="0" * 64) == ce.INVALID


def test_tampered_console():
    assert _verify_tampered(operator_console_hash="0" * 64) == ce.INVALID


def test_tampered_review():
    assert _verify_tampered(operational_review_hash="0" * 64) == ce.INVALID


def test_tampered_root():
    assert _verify_tampered(root_constitutional_hash="0" * 64) == ce.INVALID


# ── tampered layers, parametrized across bad values ─────────────────────────────

@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("badval", ["0" * 64, "tampered", "", "deadbeef"])
def test_tamper_each_layer_invalid(layer, badval):
    assert _verify_tampered(**{layer: badval}) == ce.INVALID


@pytest.mark.parametrize("badval", ["0" * 64, "tampered", "", "x"])
def test_tamper_root_invalid(badval):
    assert _verify_tampered(root_constitutional_hash=badval) == ce.INVALID


# ── verify against a wrong expected constitution -> INVALID ─────────────────────

@pytest.mark.parametrize("layer", LAYERS)
def test_wrong_expected_anchor_invalid(layer):
    wrong = dict(ce.EXPECTED_ANCHORS)
    wrong[layer] = "0" * 64
    assert ce._verify(_LIVE_ROOT, wrong, ce.EXPECTED_ROOT) == ce.INVALID


def test_wrong_expected_root_invalid():
    assert ce._verify(_LIVE_ROOT, ce.EXPECTED_ANCHORS, "0" * 64) == ce.INVALID


# ── pipeline verification (gate) ────────────────────────────────────────────────

def test_gate_returns_zero_on_valid():
    assert ce.gate() == 0


def test_gate_module_runs_clean():
    out = subprocess.run([sys.executable, "-m", "constitutional_enforcement.constitution_gate"],
                         cwd=str(_BACKEND_DIR), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "constitutional_status=VALID" in out.stdout


def test_workflow_file_exists():
    wf = _REPO_ROOT / ".github" / "workflows" / "constitutional_verification.yml"
    text = wf.read_text(encoding="utf-8")
    assert "ruff" in text and "pytest" in text and "constitution_gate" in text


# ── substrate protection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("layer", LAYERS)
def test_substrate_anchor_unchanged(layer):
    assert getattr(_LIVE_ROOT, layer) == ce.EXPECTED_ANCHORS[layer]


def test_substrate_root_unchanged():
    assert _LIVE_ROOT.root_constitutional_hash == GOLDEN_ROOT


# ── ordering / boundary ──────────────────────────────────────────────────────────

def test_enforced_layers_match_root_order():
    assert ce.ENFORCED_LAYERS == root.CONSTITUTION_ORDER


def test_enforced_layers_count():
    assert len(ce.ENFORCED_LAYERS) == 7


def test_boundary_flags():
    assert ce.EXECUTION_AUTHORITY is False
    assert ce.MUTATION_AUTHORITY is False
    assert ce.RECOMMENDATION_AUTHORITY is False
    assert ce.PREDICTION_AUTHORITY is False
    assert ce.DESCRIPTIVE_ONLY is True
    assert ce.FAIL_CLOSED is True
    assert ce.DETERMINISTIC is True


# ── cross-process identity ───────────────────────────────────────────────────────

def test_cross_process_verify_valid():
    code = "import constitutional_enforcement as ce; print(ce.verify_full_constitution())"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND_DIR),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ce.VALID


def test_cross_process_signature_identity():
    code = ("import constitutional_enforcement as ce;"
            "print(ce.build_constitution_report().verification_signature)")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND_DIR),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == GOLDEN_SIGNATURE


# ── forbidden imports ────────────────────────────────────────────────────────────

def test_no_forbidden_imports():
    pkg = Path(ce.__file__).resolve().parent
    forbidden = ("import time", "time.time", "import datetime", "from datetime",
                 "datetime.", "import random", "random.", "import uuid", "uuid.",
                 "os.environ", "getenv", "urandom", "perf_counter", "monotonic",
                 "import socket", "import requests", "import threading",
                 "import asyncio", "import multiprocessing")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders
