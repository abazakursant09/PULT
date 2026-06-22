"""Constitutional tests for the Root Constitution (Sprint 77).

Categories: cross-process determinism, tamper detection, rebuild identity, hash
stability, ordering guarantees, forbidden import scan, verification
reconstruction, substrate protection (S69-76 hashes byte-identical).

Pinned goldens. Read-only over the frozen substrate; no layer modified.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import root_constitution as root

# ── Pinned goldens ───────────────────────────────────────────────────────────────
GOLDEN_ROOT = "816632a3242098f6e545a813ab66ab6429948d316aefacb23f484d3e598f7b1d"
GOLDEN_ANCHORS = {
    "schema_baseline_revision": "47beea1df0c1",
    "logic_characterization_hash": "4445f034aa1793dc5940b9019caeef1ad8aac50d6823ae2e5fda937625cccef1",
    "runtime_envelope_hash": "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4",
    "replay_chain_hash": "9ea8fa658eb0c9ea85c80bd5fd33fa2103e5a948863b3c742cd59b361151926e",
    "runtime_application_hash": "af8700dd9fa80ceb6ea98de78e2d7893ca4977a0a7fe87db02df5ee64e158d88",
    "operator_console_hash": "9d26fd652a21449fc018a7fb5266bf5bbbb8d9e5e4eeb1c4780a1802694f370a",
    "operational_review_hash": "8372703672d25cc8a1ff424ffdfe257bff64ba30972627699180f9534186e999",
}
ANCHOR_NAMES = list(root.CONSTITUTION_ORDER)
_ANCHOR_FN = {
    "schema_baseline_revision": root.schema_baseline_revision,
    "logic_characterization_hash": root.logic_characterization_hash,
    "runtime_envelope_hash": root.runtime_envelope_hash,
    "replay_chain_hash": root.replay_chain_hash,
    "runtime_application_hash": root.runtime_application_hash,
    "operator_console_hash": root.operator_console_hash,
    "operational_review_hash": root.operational_review_hash,
}


# ── hash stability (golden) ──────────────────────────────────────────────────────

def test_root_hash_golden():
    assert root.build_root_constitution().root_constitutional_hash == GOLDEN_ROOT


@pytest.mark.parametrize("name", ANCHOR_NAMES)
def test_anchor_golden(name):
    assert getattr(root.build_root_constitution(), name) == GOLDEN_ANCHORS[name]


@pytest.mark.parametrize("i", list(range(40)))
def test_root_hash_stable_repeat(i):
    assert root.build_root_constitution().root_constitutional_hash == GOLDEN_ROOT


@pytest.mark.parametrize("name", ANCHOR_NAMES)
@pytest.mark.parametrize("i", list(range(8)))
def test_anchor_stable_repeat(name, i):
    assert _ANCHOR_FN[name]() == GOLDEN_ANCHORS[name]


def test_root_is_64_hex():
    h = root.build_root_constitution().root_constitutional_hash
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ── verification reconstruction ──────────────────────────────────────────────────

def test_verify_clean_is_valid():
    assert root.verify_root_constitution(root.build_root_constitution()) == root.VALID


def test_verify_returns_only_valid_or_invalid():
    rc = root.build_root_constitution()
    assert root.verify_root_constitution(rc) in (root.VALID, root.INVALID)


# ── tamper detection ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ANCHOR_NAMES)
def test_tamper_anchor_is_invalid(name):
    rc = root.build_root_constitution()
    tampered = dataclasses.replace(rc, **{name: "0" * 64})
    assert root.verify_root_constitution(tampered) == root.INVALID


def test_tamper_root_hash_is_invalid():
    rc = root.build_root_constitution()
    tampered = dataclasses.replace(rc, root_constitutional_hash="0" * 64)
    assert root.verify_root_constitution(tampered) == root.INVALID


# ── rebuild identity ─────────────────────────────────────────────────────────────

def test_rebuild_identity_full():
    assert root.build_root_constitution() == root.build_root_constitution()


@pytest.mark.parametrize("name", ANCHOR_NAMES)
def test_rebuild_identity_per_anchor(name):
    assert getattr(root.build_root_constitution(), name) == getattr(root.build_root_constitution(), name)


# ── ordering guarantees ──────────────────────────────────────────────────────────

def test_constitution_order_length():
    assert len(root.CONSTITUTION_ORDER) == 7


def test_constitution_order_unique():
    assert len(set(root.CONSTITUTION_ORDER)) == 7


def test_constitution_order_matches_dataclass_fields():
    fields = [f.name for f in dataclasses.fields(root.RootConstitution)]
    assert fields[:7] == list(root.CONSTITUTION_ORDER)
    assert fields[7] == "root_constitutional_hash"


def test_fold_is_order_sensitive():
    anchors = GOLDEN_ANCHORS
    forward = root.fold_root(anchors)
    # swapping two anchor VALUES changes the fold (ordering/content bound)
    swapped = dict(anchors)
    swapped["runtime_envelope_hash"], swapped["replay_chain_hash"] = (
        anchors["replay_chain_hash"], anchors["runtime_envelope_hash"])
    assert root.fold_root(swapped) != forward


# ── immutability / boundary ──────────────────────────────────────────────────────

def test_root_constitution_is_frozen():
    rc = root.build_root_constitution()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rc.root_constitutional_hash = "x"  # type: ignore[misc]


def test_boundary_flags():
    assert root.EXECUTION_AUTHORITY is False
    assert root.MUTATION_AUTHORITY is False
    assert root.RECOMMENDATION_AUTHORITY is False
    assert root.PREDICTION_AUTHORITY is False
    assert root.DESCRIPTIVE_ONLY is True
    assert root.DETERMINISTIC is True


# ── substrate protection (S69-76 hashes byte-identical) ──────────────────────────

@pytest.mark.parametrize("name", ANCHOR_NAMES)
def test_substrate_hash_unchanged(name):
    # Each layer's constitutional anchor still equals its independently-known
    # golden -> Sprint 77 did not perturb any frozen layer.
    assert _ANCHOR_FN[name]() == GOLDEN_ANCHORS[name]


def test_envelope_golden_matches_sprint73():
    assert root.runtime_envelope_hash() == "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


def test_runtime_application_golden_matches_sprint75():
    assert root.runtime_application_hash() == "af8700dd9fa80ceb6ea98de78e2d7893ca4977a0a7fe87db02df5ee64e158d88"


def test_operator_console_golden_matches_sprint75():
    assert root.operator_console_hash() == "9d26fd652a21449fc018a7fb5266bf5bbbb8d9e5e4eeb1c4780a1802694f370a"


# ── forbidden import scan ────────────────────────────────────────────────────────

def test_no_forbidden_imports():
    pkg = Path(root.__file__).resolve().parent
    forbidden = ("import time", "time.time", "import datetime", "from datetime",
                 "datetime.", "import random", "random.", "import uuid", "uuid.",
                 "os.environ", "getenv", "urandom", "perf_counter", "monotonic",
                 "import socket", "import requests", "import threading",
                 "import asyncio", "import multiprocessing", "import subprocess")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


# ── cross-process determinism ────────────────────────────────────────────────────

def test_cross_process_root_identity():
    code = ("import root_constitution as root;"
            "print(root.build_root_constitution().root_constitutional_hash)")
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == GOLDEN_ROOT


def test_cross_process_verify_valid():
    code = ("import root_constitution as root;"
            "rc=root.build_root_constitution();"
            "print(root.verify_root_constitution(rc))")
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == root.VALID
