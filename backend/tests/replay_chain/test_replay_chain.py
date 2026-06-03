"""End-to-End Replay Chain constitutional tests (Sprint 74).

Freezes the full chain: events -> signal_set -> cognition topology -> runtime
envelope -> replay_chain_hash, per canonical fixture. Pinned via committed
fixture snapshots under tests/replay_chain_fixtures/.

Regenerate intentionally: REPLAY_UPDATE=1 python -m pytest tests/replay_chain
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import replay_chain as rc

_FIXDIR = Path(__file__).resolve().parents[1] / "replay_chain_fixtures"


def _freeze(fixture) -> dict:
    ch = rc.build_replay_chain(fixture.event_log, fixture.baseline_anchor)
    return {
        "event_log": [dict(e) for e in fixture.event_log],
        "baseline_anchor": fixture.baseline_anchor,
        "signal_set": ch.signal_set,
        "cognition_topology": ch.cognition_topology,
        "event_log_hash": ch.event_log_hash,
        "signal_set_signature": ch.signal_set_signature,
        "cognition_hash": ch.cognition_hash,
        "envelope_hash": ch.envelope_hash,
        "replay_chain_hash": ch.replay_chain_hash,
    }


def _maybe_regenerate():
    if os.environ.get("REPLAY_UPDATE") == "1":
        _FIXDIR.mkdir(parents=True, exist_ok=True)
        for f in rc.ALL_FIXTURES:
            with (_FIXDIR / f"{f.name}.json").open("w", encoding="utf-8") as fh:
                json.dump(_freeze(f), fh, ensure_ascii=False, indent=2, sort_keys=True)


_maybe_regenerate()
_NAMES = [f.name for f in rc.ALL_FIXTURES]


def _load(name: str) -> dict:
    with (_FIXDIR / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


# ── Frozen fixture hashes ───────────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("REPLAY_UPDATE") == "1", reason="regenerating")
@pytest.mark.parametrize("name", _NAMES)
def test_fixture_chain_frozen(name):
    ref = _load(name)
    f = rc.FIXTURES_BY_NAME[name]
    live = rc.build_replay_chain(f.event_log, f.baseline_anchor)
    assert live.event_log_hash == ref["event_log_hash"]
    assert live.signal_set_signature == ref["signal_set_signature"]
    assert live.cognition_hash == ref["cognition_hash"]
    assert live.envelope_hash == ref["envelope_hash"]
    assert live.replay_chain_hash == ref["replay_chain_hash"]
    assert len(live.replay_chain_hash) == 64


# ── identical inputs -> identical replay_chain_hash ─────────────────────────────

@pytest.mark.parametrize("name", _NAMES)
def test_determinism_same_process(name):
    f = rc.FIXTURES_BY_NAME[name]
    a = rc.build_replay_chain(f.event_log, f.baseline_anchor)
    b = rc.build_replay_chain(f.event_log, f.baseline_anchor)
    assert a.replay_chain_hash == b.replay_chain_hash


def test_distinct_fixtures_distinct_hashes():
    hashes = {rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
              for f in rc.ALL_FIXTURES}
    assert len(hashes) == len(rc.ALL_FIXTURES)


# ── cross-process identity ──────────────────────────────────────────────────────

def test_cross_process_identity():
    f = rc.FIXTURES_BY_NAME["replay_instability_burst"]
    expected = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    code = (
        "import replay_chain as rc;"
        "f=rc.FIXTURES_BY_NAME['replay_instability_burst'];"
        "print(rc.build_replay_chain(f.event_log,f.baseline_anchor).replay_chain_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


# ── tamper detection ────────────────────────────────────────────────────────────

def _att():
    f = rc.FIXTURES_BY_NAME["cascading_failure"]
    return rc.attest_replay(f.event_log, f.baseline_anchor)


def test_clean_attestation_verifies():
    assert rc.verify_attestation(_att()) is True


def test_tamper_event_fails():
    att = _att()
    bad_log = (dict(att.event_log[0], weight=999),) + att.event_log[1:]
    tampered = dataclasses.replace(att, event_log=bad_log)
    assert rc.verify_attestation(tampered) is False


def test_tamper_signal_set_fails():
    att = _att()
    bad_chain = dataclasses.replace(att.chain, signal_set_signature="0" * 64)
    assert rc.verify_attestation(dataclasses.replace(att, chain=bad_chain)) is False


def test_tamper_cognition_hash_fails():
    att = _att()
    bad_chain = dataclasses.replace(att.chain, cognition_hash="0" * 64)
    assert rc.verify_attestation(dataclasses.replace(att, chain=bad_chain)) is False


def test_tamper_envelope_hash_fails():
    att = _att()
    bad_chain = dataclasses.replace(att.chain, envelope_hash="0" * 64)
    assert rc.verify_attestation(dataclasses.replace(att, chain=bad_chain)) is False


def test_tamper_chain_hash_fails():
    att = _att()
    bad_chain = dataclasses.replace(att.chain, replay_chain_hash="0" * 64)
    assert rc.verify_attestation(dataclasses.replace(att, chain=bad_chain)) is False


# ── no clocks / randomness / uuids / env in the verifier core ───────────────────

def test_no_forbidden_constructs():
    pkg = Path(rc.__file__).resolve().parent
    # Precise executable tokens (avoid matching doctrine docstrings that *name*
    # forbidden constructs like "no clocks, uuids, randomness").
    forbidden = ("import random", "random.random", "random.", "import time",
                 "time.time", "time.monotonic", "import datetime", "datetime.",
                 "from datetime", "import uuid", "uuid.", "uuid4",
                 "os.environ", "os.getenv", "getenv(", "urandom",
                 "perf_counter", "monotonic(", "import socket", "socket.",
                 "import requests", "requests.", "httpx.")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders
