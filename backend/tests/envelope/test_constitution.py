"""Constitutional tests for the Runtime Envelope (Sprint 73).

Freezes: replay identity, envelope determinism, boot reproducibility, session
reproducibility, hash stability, replay-boundary doctrine, and the no-clock /
no-randomness / no-hidden-state guarantee.

Golden hashes are pinned. A change to envelope construction breaks these tests.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import runtime_envelope as re
from runtime_envelope.replay_boundary import ReplayBoundaryViolation

# Fixed anchors -> golden identity (pinned).
_FIXED = dict(
    collector_signature="COLLECTOR_SIG",
    signal_set_signature="SIGNALSET_SIG",
    cognition_v2_runtime_hash="COGNITION_HASH",
    baseline_anchor="47beea1df0c1",
)
GOLDEN_BOOT_ID = "171ac37bc3cd6701ade65631d8b382f5f718771b0bef7bf9ac714fb205d78ab6"
GOLDEN_SESSION_ID = "10785edaae2dd368391d63e982f0ab9b577c45b50b402ee1146ab7607fad22b0"
GOLDEN_TOPOLOGY = "bfa769f98471dc36286cfc1e447f7a4c45e069b121263fb3147d247d50b6c678"
GOLDEN_ENVELOPE_HASH = "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


# ── Hash stability (golden) ────────────────────────────────────────────────────

def test_envelope_hash_golden():
    env = re.build_runtime_envelope(**_FIXED)
    assert env.runtime_envelope_hash == GOLDEN_ENVELOPE_HASH
    assert env.boot_id == GOLDEN_BOOT_ID
    assert env.session_id == GOLDEN_SESSION_ID
    assert env.topology_attestation == GOLDEN_TOPOLOGY
    assert len(env.runtime_envelope_hash) == 64  # SHA-256 hex


# ── Envelope determinism ───────────────────────────────────────────────────────

def test_envelope_determinism():
    a = re.build_runtime_envelope(**_FIXED)
    b = re.build_runtime_envelope(**_FIXED)
    assert a == b
    assert a.runtime_envelope_hash == b.runtime_envelope_hash


def test_hash_is_dict_order_independent():
    p1 = {"a": 1, "b": {"x": 1, "y": 2}}
    p2 = {"b": {"y": 2, "x": 1}, "a": 1}
    assert re.canonical_bytes(p1) == re.canonical_bytes(p2)
    assert re.sha256_hex(p1) == re.sha256_hex(p2)


# ── Boot reproducibility ───────────────────────────────────────────────────────

def test_boot_reproducibility():
    same = re.compute_boot_id(baseline_anchor="B", collector_signature="C",
                              cognition_v2_runtime_hash="H")
    again = re.compute_boot_id(baseline_anchor="B", collector_signature="C",
                               cognition_v2_runtime_hash="H")
    assert same == again
    diff = re.compute_boot_id(baseline_anchor="B2", collector_signature="C",
                              cognition_v2_runtime_hash="H")
    assert diff != same


# ── Session reproducibility ────────────────────────────────────────────────────

def test_session_reproducibility():
    s1 = re.compute_session_id(boot_id="BOOT", signal_set_signature="S")
    s2 = re.compute_session_id(boot_id="BOOT", signal_set_signature="S")
    assert s1 == s2
    assert re.compute_session_id(boot_id="BOOT", signal_set_signature="S2") != s1
    assert re.compute_session_id(boot_id="BOOT2", signal_set_signature="S") != s1


# ── Replay identity ────────────────────────────────────────────────────────────

def test_replay_identity_verifies():
    env = re.build_runtime_envelope(**_FIXED)
    assert re.verify_attestation(env) is True


def test_tampered_envelope_fails_verification():
    env = re.build_runtime_envelope(**_FIXED)
    tampered = dataclasses.replace(env, runtime_envelope_hash="0" * 64)
    assert re.verify_attestation(tampered) is False
    tampered_boot = dataclasses.replace(env, boot_id="deadbeef")
    assert re.verify_attestation(tampered_boot) is False


# ── Replay boundary doctrine ───────────────────────────────────────────────────

def test_replay_boundary_accepts_envelope_payload():
    env = re.build_runtime_envelope(**_FIXED)
    re.assert_replay_safe(env.as_payload())  # must not raise


def test_replay_boundary_rejects_nondeterministic_keys():
    for bad in ("wall_clock_time", "process_id", "random_seed",
                "environment_variables", "hostname", "log_timestamp"):
        try:
            re.assert_replay_safe({bad: "x"})
            assert False, f"expected rejection for {bad}"
        except ReplayBoundaryViolation:
            pass


def test_replay_scope_partitions_are_disjoint():
    assert set(re.REPLAY_SCOPE).isdisjoint(set(re.NON_REPLAY_SCOPE))
    for k in re.NON_REPLAY_SCOPE:
        assert re.classify(k) == "outside"


# ── No clocks / randomness / hidden state ──────────────────────────────────────

_PKG = Path(re.__file__).resolve().parent
_FORBIDDEN_TOKENS = (
    "import random", "random.", "import time", "time.time", "time.monotonic",
    "import datetime", "from datetime", "datetime.", "uuid", "os.environ",
    "getenv", "perf_counter", "monotonic", "urandom",
)


def test_no_forbidden_imports_in_core():
    offenders = []
    for src in _PKG.glob("*.py"):
        # replay_boundary.py *names* the forbidden constructs as doctrine strings
        # (it is the screen definition); exclude it from the literal token scan.
        if src.name == "replay_boundary.py":
            continue
        text = src.read_text(encoding="utf-8")
        for tok in _FORBIDDEN_TOKENS:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, f"non-deterministic constructs found: {offenders}"


# ── Cross-process determinism (separate interpreter) ───────────────────────────

def test_cross_process_hash_identity():
    code = (
        "import runtime_envelope as re;"
        "e=re.build_runtime_envelope(collector_signature='COLLECTOR_SIG',"
        "signal_set_signature='SIGNALSET_SIG',cognition_v2_runtime_hash='COGNITION_HASH',"
        "baseline_anchor='47beea1df0c1');print(e.runtime_envelope_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_PKG.parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == GOLDEN_ENVELOPE_HASH
