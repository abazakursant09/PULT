"""Constitutional tests for the Operational Review Layer (Sprint 76).

Observe -> Review -> Record. Categories: determinism, replay reconstruction,
append-only behavior, descriptive-only vocabulary, forbidden recommendation
vocabulary, forbidden prediction vocabulary, hash stability, cross-process
identity, tamper detection.

Read-only over the real substrate (Runtime Application). Golden per-fixture
review hashes pinned under tests/operational_review_fixtures/.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import operational_review as orv
import runtime_application as ra
import replay_chain as rc

FIXTURES = [(f.name, f.event_log) for f in rc.ALL_FIXTURES]
NAMES = [n for n, _ in FIXTURES]
_FIXDIR = Path(__file__).resolve().parents[1] / "operational_review_fixtures"

# All-six-session pinned review_hash (single source order).
ALL_SIX_REVIEW_HASH = "995ca32545b09fce91fceb21e146e9659ed2b1e93ad1821c110fc0561526a807"

_REC_FORBIDDEN = ("should", "recommend", "must ", "advise", "suggest",
                  "investigate", "action", "rank", "prioritize")
_PRED_FORBIDDEN = ("likely", "expected", "will ", "predict", "forecast",
                   "degradation", "future", "risk", "failure", "going to")

if os.environ.get("RUNTIME_REVIEW_UPDATE") == "1":
    _FIXDIR.mkdir(parents=True, exist_ok=True)
    for _n, _log in FIXTURES:
        sess = orv.build_review_session([(_n, _log)])
        with (_FIXDIR / f"{_n}.json").open("w", encoding="utf-8") as fh:
            json.dump({"review_hash": sess.review_hash,
                       "snapshot": sess.snapshots[0].to_dict()},
                      fh, ensure_ascii=False, indent=2, sort_keys=True)


def _load(name):
    with (_FIXDIR / f"{name}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def _single(name, log):
    return orv.build_review_session([(name, log)])


# ── determinism ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_session_deterministic(name, log):
    assert _single(name, log).review_hash == _single(name, log).review_hash


@pytest.mark.parametrize("name,log", FIXTURES)
def test_review_hash_64_hex(name, log):
    h = _single(name, log).review_hash
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


@pytest.mark.parametrize("name,log", FIXTURES)
def test_findings_hash_deterministic(name, log):
    app = ra.build_runtime_application(log)
    f = orv.derive_findings(app)
    assert orv.findings_hash(f) == orv.findings_hash(f)


@pytest.mark.parametrize("name,log", FIXTURES)
def test_snapshot_hash_deterministic(name, log):
    assert orv.build_snapshot(name, log).snapshot_hash == orv.build_snapshot(name, log).snapshot_hash


def test_multi_session_deterministic():
    assert orv.build_review_session(FIXTURES).review_hash == orv.build_review_session(FIXTURES).review_hash


def test_order_changes_review_hash():
    a = orv.build_review_session(FIXTURES).review_hash
    b = orv.build_review_session(list(reversed(FIXTURES))).review_hash
    assert a != b


def test_distinct_fixtures_distinct_review_hashes():
    hs = {_single(n, log).review_hash for n, log in FIXTURES}
    assert len(hs) == len(FIXTURES)


# ── hash stability (golden) ──────────────────────────────────────────────────────

def test_all_six_review_hash_pinned():
    assert orv.build_review_session(FIXTURES).review_hash == ALL_SIX_REVIEW_HASH


@pytest.mark.skipif(os.environ.get("RUNTIME_REVIEW_UPDATE") == "1", reason="regenerating")
@pytest.mark.parametrize("name,log", FIXTURES)
def test_golden_review_hash(name, log):
    assert _single(name, log).review_hash == _load(name)["review_hash"]


@pytest.mark.skipif(os.environ.get("RUNTIME_REVIEW_UPDATE") == "1", reason="regenerating")
@pytest.mark.parametrize("name,log", FIXTURES)
def test_golden_snapshot(name, log):
    assert _single(name, log).snapshots[0].to_dict() == _load(name)["snapshot"]


# ── snapshot binds the reviewed runtime_application_hash ─────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_snapshot_binds_application_hash(name, log):
    snap = orv.build_snapshot(name, log)
    assert snap.runtime_application_hash == ra.build_runtime_application(log).runtime_application_hash


@pytest.mark.parametrize("name,log", FIXTURES)
def test_findings_match_derive(name, log):
    snap = orv.build_snapshot(name, log)
    assert snap.findings == orv.derive_findings(ra.build_runtime_application(log))


# ── descriptive-only vocabulary ──────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_findings_text_from_catalog(name, log):
    catalog = set(orv.FINDING_CATALOG.values())
    for f in orv.build_snapshot(name, log).findings:
        assert f.descriptive_text in catalog


def test_catalog_is_descriptive_only():
    for text in orv.FINDING_CATALOG.values():
        low = text.lower()
        assert all(tok not in low for tok in _REC_FORBIDDEN)
        assert all(tok not in low for tok in _PRED_FORBIDDEN)


# ── forbidden recommendation vocabulary (in findings) ────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_no_recommendation_vocab(name, log):
    for f in orv.build_snapshot(name, log).findings:
        low = f.descriptive_text.lower()
        assert all(tok not in low for tok in _REC_FORBIDDEN), f.descriptive_text


# ── forbidden prediction vocabulary (in findings) ────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_no_prediction_vocab(name, log):
    for f in orv.build_snapshot(name, log).findings:
        low = f.descriptive_text.lower()
        assert all(tok not in low for tok in _PRED_FORBIDDEN), f.descriptive_text


# ── append-only behavior ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_ledger_record_appends_one(name, log):
    led = orv.ReviewLedger()
    snap = orv.build_snapshot(name, log)
    led2 = led.record(snap)
    assert led.length == 0 and led2.length == 1
    assert led2.entries[0].review_sequence == 0


@pytest.mark.parametrize("name,log", FIXTURES)
def test_ledger_prefix_preserved(name, log):
    led = orv.ReviewLedger().record(orv.build_snapshot("a", log))
    before = led.entries
    led2 = led.record(orv.build_snapshot(name, log))
    assert led2.entries[:1] == before
    assert led2.entries[1].review_sequence == 1


@pytest.mark.parametrize("name,log", FIXTURES)
def test_ledger_records_application_hash(name, log):
    snap = orv.build_snapshot(name, log)
    entry = orv.ReviewLedger().record(snap).entries[0]
    assert entry.runtime_application_hash == snap.runtime_application_hash
    assert entry.snapshot_hash == snap.snapshot_hash


def test_session_snapshot_order_preserved():
    sess = orv.build_review_session(FIXTURES)
    assert [s.label for s in sess.snapshots] == NAMES
    assert [e.review_sequence for e in sess.ledger.entries] == list(range(len(FIXTURES)))


# ── replay reconstruction ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_attestation_verifies(name, log):
    assert orv.verify_attestation(orv.attest_review([(name, log)])) is True


def test_multi_attestation_verifies():
    assert orv.verify_attestation(orv.attest_review(FIXTURES)) is True


# ── tamper detection ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_tamper_input_fails(name, log):
    if not log:
        pytest.skip("empty")
    att = orv.attest_review([(name, log)])
    bad_log = (dict(att.reviews[0][1][0], weight=12345),) + att.reviews[0][1][1:]
    bad = dataclasses.replace(att, reviews=((att.reviews[0][0], bad_log),))
    assert orv.verify_attestation(bad) is False


@pytest.mark.parametrize("name,log", FIXTURES)
def test_tamper_review_hash_fails(name, log):
    att = orv.attest_review([(name, log)])
    bad_session = dataclasses.replace(att.session, review_hash="0" * 64)
    assert orv.verify_attestation(dataclasses.replace(att, session=bad_session)) is False


@pytest.mark.parametrize("name,log", FIXTURES)
def test_tamper_snapshot_fails(name, log):
    att = orv.attest_review([(name, log)])
    snap = att.session.snapshots[0]
    bad_snap = dataclasses.replace(snap, snapshot_hash="0" * 64)
    bad_session = dataclasses.replace(att.session, snapshots=(bad_snap,))
    assert orv.verify_attestation(dataclasses.replace(att, session=bad_session)) is False


# ── immutability / boundary ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_session_is_frozen(name, log):
    sess = _single(name, log)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sess.review_hash = "x"  # type: ignore[misc]


@pytest.mark.parametrize("name,log", FIXTURES)
def test_finding_is_frozen(name, log):
    f = orv.build_snapshot(name, log).findings
    if not f:
        pytest.skip("no findings")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f[0].subject = "x"  # type: ignore[misc]


def test_boundary_flags():
    assert orv.EXECUTION_AUTHORITY is False
    assert orv.MUTATION_AUTHORITY is False
    assert orv.RECOMMENDATION_AUTHORITY is False
    assert orv.PREDICTION_AUTHORITY is False
    assert orv.RANKING_AUTHORITY is False
    assert orv.DESCRIPTIVE_ONLY is True
    assert orv.APPEND_ONLY is True
    assert orv.DETERMINISTIC is True


# ── governance separation / substrate invariance ────────────────────────────────

@pytest.mark.parametrize("name,log", FIXTURES)
def test_substrate_unaffected(name, log):
    f = rc.FIXTURES_BY_NAME[name]
    h1 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    orv.build_review_session([(name, log)])
    h2 = rc.build_replay_chain(f.event_log, f.baseline_anchor).replay_chain_hash
    assert h1 == h2


# ── no forbidden imports ─────────────────────────────────────────────────────────

def test_no_forbidden_imports():
    pkg = Path(orv.__file__).resolve().parent
    forbidden = ("import threading", "import asyncio", "import multiprocessing",
                 "import socket", "import subprocess", "import requests",
                 "from threading", "from asyncio")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


# ── cross-process identity ───────────────────────────────────────────────────────

def test_cross_process_identity():
    expected = orv.build_review_session(FIXTURES).review_hash
    code = (
        "import operational_review as orv, replay_chain as rc;"
        "r=[(f.name,f.event_log) for f in rc.ALL_FIXTURES];"
        "print(orv.build_review_session(r).review_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code],
                         cwd=str(Path(__file__).resolve().parents[1].parent),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected
