"""Runtime Binding adapter tests (Sprint 80).

Categories: cross-process determinism, 10-run identity, tamper detection,
ordering normalization, uuid stripping, timestamp stripping, replay
reconstruction, attestation verification, fail-closed behavior, substrate
protection. Read-only over the frozen substrate; no layer modified.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import runtime_binding as rb
import root_constitution as root
import constitutional_enforcement as ce

_BACKEND = Path(__file__).resolve().parents[2]

# Canonical raw UserEvent stream (uuids, timestamps, scrambled order).
CANONICAL_RAW = [
    {"event_type": "recurrence", "entity_id": "margin_crisis:wildberries:SKU-a1b2c3d4e5f67890a1b2c3d4e5f67890",
     "metadata_json": '{"weight": 6}', "created_at": "2026-05-31T10:00:00", "id": "evt-1", "user_id": "u1"},
    {"event_type": "margin_pressure", "entity_id": "margin_crisis:wildberries:A",
     "metadata_json": '{"weight": 8, "marketplace": "wildberries"}', "created_at": "2026-05-30", "user_id": "u1"},
    {"event_type": "cascade", "entity_id": "high_ad_spend:ozon:B-550e8400e29b41d4a716446655440000",
     "metadata_json": None, "created_at": "2026-05-29"},
]

GOLDEN_BINDING_HASH = "57a136345744ab10637684af996df477d2d18eb871154d49180ae8bfd8bce4c8"
# Sprint 77/78 frozen substrate goldens.
GOLDEN_ROOT = "816632a3242098f6e545a813ab66ab6429948d316aefacb23f484d3e598f7b1d"
GOLDEN_ENVELOPE = "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


# ── determinism / 10-run identity ────────────────────────────────────────────────

@pytest.mark.parametrize("i", list(range(50)))
def test_binding_hash_stable(i):
    assert rb.build_runtime_binding(CANONICAL_RAW).runtime_binding_hash == GOLDEN_BINDING_HASH


@pytest.mark.parametrize("i", list(range(10)))
def test_ten_run_identity(i):
    b = rb.build_runtime_binding(CANONICAL_RAW)
    assert b.runtime_binding_hash == GOLDEN_BINDING_HASH
    assert b.canonical_events == rb.build_runtime_binding(CANONICAL_RAW).canonical_events


def test_binding_hash_64_hex():
    h = rb.build_runtime_binding(CANONICAL_RAW).runtime_binding_hash
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ── ordering normalization ───────────────────────────────────────────────────────

_PERMUTATIONS = [
    [0, 1, 2], [2, 1, 0], [1, 0, 2], [0, 2, 1], [2, 0, 1], [1, 2, 0],
]


@pytest.mark.parametrize("perm", _PERMUTATIONS)
def test_order_invariant(perm):
    permuted = [CANONICAL_RAW[i] for i in perm]
    assert rb.build_runtime_binding(permuted).runtime_binding_hash == GOLDEN_BINDING_HASH


@pytest.mark.parametrize("perm", _PERMUTATIONS)
def test_canonical_events_sorted(perm):
    permuted = [CANONICAL_RAW[i] for i in perm]
    ev = rb.build_runtime_binding(permuted).canonical_events
    keys = [(e["event_type"], e["entity"], e["weight"], e["marketplace"]) for e in ev]
    assert keys == sorted(keys)


# ── uuid stripping ───────────────────────────────────────────────────────────────

_UUID_CASES = [
    ("margin_crisis:wb:SKU-550e8400-e29b-41d4-a716-446655440000", "margin_crisis:wb:SKU"),
    ("margin_crisis:wb:SKU-550e8400e29b41d4a716446655440000", "margin_crisis:wb:SKU"),
    ("x:y:a1b2c3d4e5f678901234567890abcdef", "x:y"),
    ("cat:mp:plain", "cat:mp:plain"),
    ("cat:mp:SKU123", "cat:mp:SKU123"),
    ("a:b:c", "a:b:c"),
    ("margin_crisis:ozon:Z", "margin_crisis:ozon:Z"),
]


@pytest.mark.parametrize("raw_entity,expected", _UUID_CASES)
def test_uuid_stripping(raw_entity, expected):
    ev = rb.canonicalize_event({"event_type": "x", "entity_id": raw_entity})
    assert ev["entity"] == expected


@pytest.mark.parametrize("raw_entity,expected", _UUID_CASES)
def test_strip_uuid_function(raw_entity, expected):
    assert rb.strip_uuid(raw_entity) == expected


# ── timestamp stripping (created_at irrelevant) ──────────────────────────────────

_TIMESTAMPS = ["2026-01-01", "2026-05-31T10:00:00", "2020-12-31T23:59:59",
               "", "1999-01-01", None, "2026-06-15T08:30:00Z"]


@pytest.mark.parametrize("ts", _TIMESTAMPS)
def test_timestamp_does_not_affect_canonical(ts):
    ev = {"event_type": "margin_pressure", "entity_id": "margin_crisis:wb:A",
          "metadata_json": '{"weight": 8}', "created_at": ts}
    base = {"event_type": "margin_pressure", "entity_id": "margin_crisis:wb:A",
            "metadata_json": '{"weight": 8}'}
    assert rb.canonicalize_event(ev) == rb.canonicalize_event(base)


@pytest.mark.parametrize("ts", _TIMESTAMPS)
def test_timestamp_does_not_affect_binding_hash(ts):
    s1 = [{"event_type": "a", "entity_id": "margin_crisis:wb:A", "metadata_json": '{"weight": 3}', "created_at": ts}]
    s2 = [{"event_type": "a", "entity_id": "margin_crisis:wb:A", "metadata_json": '{"weight": 3}'}]
    assert rb.build_runtime_binding(s1).runtime_binding_hash == rb.build_runtime_binding(s2).runtime_binding_hash


# ── canonical projection golden ──────────────────────────────────────────────────

def test_canonical_events_golden():
    ev = rb.build_runtime_binding(CANONICAL_RAW).canonical_events
    assert ev == (
        {"event_type": "cascade", "entity": "high_ad_spend:ozon:B", "weight": 0, "marketplace": ""},
        {"event_type": "margin_pressure", "entity": "margin_crisis:wildberries:A", "weight": 8, "marketplace": "wildberries"},
        {"event_type": "recurrence", "entity": "margin_crisis:wildberries:SKU", "weight": 6, "marketplace": ""},
    )


def test_summary_counts():
    b = rb.build_runtime_binding(CANONICAL_RAW)
    assert b.canonical_event_count == 3
    assert b.adapter_version == rb.ADAPTER_VERSION


# ── fail-closed behavior ─────────────────────────────────────────────────────────

_BAD = [
    {"event_type": "x", "entity_id": "e", "bogus": 1},          # unknown field
    {"event_type": "x"},                                         # missing entity_id
    {"entity_id": "e"},                                          # missing event_type
    {"event_type": "x", "entity_id": "e", "metadata_json": "{bad"},   # invalid json
    {"event_type": "x", "entity_id": "e", "metadata_json": "[1,2]"},  # non-object json
    {"event_type": "x", "entity_id": "e", "metadata_json": '{"weight": "5"}'},  # bad weight type
    {"event_type": "x", "entity_id": "e", "metadata_json": '{"marketplace": 1}'},  # bad marketplace
    {"event_type": "x", "entity_id": "a1b2c3d4e5f678901234567890abcdef"},  # entity empty after strip
    {"event_type": "", "entity_id": "e"},                        # empty event_type
    "not-a-dict-or-event",                                        # wrong type
]


@pytest.mark.parametrize("bad", _BAD)
def test_fail_closed_raises(bad):
    with pytest.raises(rb.BindingViolation):
        rb.build_runtime_binding([bad])


@pytest.mark.parametrize("bad", _BAD)
def test_fail_closed_in_mixed_stream(bad):
    stream = [CANONICAL_RAW[1], bad]
    with pytest.raises(rb.BindingViolation):
        rb.build_runtime_binding(stream)


# ── attestation verification / tamper ────────────────────────────────────────────

def test_attestation_valid():
    assert rb.verify_binding(rb.attest_binding(CANONICAL_RAW)) == rb.VALID


@pytest.mark.parametrize("i", list(range(10)))
def test_attestation_valid_repeat(i):
    assert rb.verify_binding(rb.attest_binding(CANONICAL_RAW)) == rb.VALID


def test_tamper_raw_event_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = (dict(att.raw_events[0], metadata_json='{"weight": 999}'),) + att.raw_events[1:]
    assert rb.verify_binding(dataclasses.replace(att, raw_events=bad)) == rb.INVALID


def test_tamper_binding_hash_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = dataclasses.replace(att.binding, runtime_binding_hash="0" * 64)
    assert rb.verify_binding(dataclasses.replace(att, binding=bad)) == rb.INVALID


def test_tamper_app_hash_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = dataclasses.replace(att.binding, runtime_application_hash="0" * 64)
    assert rb.verify_binding(dataclasses.replace(att, binding=bad)) == rb.INVALID


def test_tamper_replay_hash_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = dataclasses.replace(att.binding, replay_chain_hash="0" * 64)
    assert rb.verify_binding(dataclasses.replace(att, binding=bad)) == rb.INVALID


def test_tamper_review_hash_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = dataclasses.replace(att.binding, operational_review_hash="0" * 64)
    assert rb.verify_binding(dataclasses.replace(att, binding=bad)) == rb.INVALID


def test_tamper_canonical_events_invalid():
    att = rb.attest_binding(CANONICAL_RAW)
    bad = dataclasses.replace(att.binding, canonical_events=())
    assert rb.verify_binding(dataclasses.replace(att, binding=bad)) == rb.INVALID


def test_verify_only_valid_or_invalid():
    assert rb.verify_binding(rb.attest_binding(CANONICAL_RAW)) in (rb.VALID, rb.INVALID)


# ── replay reconstruction ────────────────────────────────────────────────────────

def test_replay_reconstruction_equal():
    assert rb.build_runtime_binding(CANONICAL_RAW) == rb.build_runtime_binding(CANONICAL_RAW)


@pytest.mark.parametrize("perm", _PERMUTATIONS)
def test_replay_reconstruction_order_invariant(perm):
    permuted = [CANONICAL_RAW[i] for i in perm]
    assert rb.build_runtime_binding(permuted) == rb.build_runtime_binding(CANONICAL_RAW)


# ── substrate protection ─────────────────────────────────────────────────────────

def test_substrate_root_unchanged_after_binding():
    rb.build_runtime_binding(CANONICAL_RAW)
    assert root.build_root_constitution().root_constitutional_hash == GOLDEN_ROOT


def test_substrate_envelope_unchanged_after_binding():
    rb.build_runtime_binding(CANONICAL_RAW)
    assert root.runtime_envelope_hash() == GOLDEN_ENVELOPE


def test_constitution_remains_valid_after_binding():
    rb.build_runtime_binding(CANONICAL_RAW)
    assert ce.verify_full_constitution() == ce.VALID


@pytest.mark.parametrize("layer", list(ce.ENFORCED_LAYERS))
def test_each_substrate_anchor_unchanged(layer):
    rb.build_runtime_binding(CANONICAL_RAW)
    assert getattr(root.build_root_constitution(), layer) == ce.EXPECTED_ANCHORS[layer]


# ── boundary / immutability / imports ────────────────────────────────────────────

def test_boundary_flags():
    assert rb.EXECUTION_AUTHORITY is False
    assert rb.MUTATION_AUTHORITY is False
    assert rb.RECOMMENDATION_AUTHORITY is False
    assert rb.PREDICTION_AUTHORITY is False
    assert rb.READ_ONLY is True
    assert rb.FAIL_CLOSED is True
    assert rb.DETERMINISTIC is True


def test_binding_is_frozen():
    b = rb.build_runtime_binding(CANONICAL_RAW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.runtime_binding_hash = "x"  # type: ignore[misc]


def test_no_forbidden_imports():
    pkg = Path(rb.__file__).resolve().parent
    forbidden = ("import time", "time.time", "import datetime", "from datetime",
                 "datetime.", "import uuid", "uuid.", "uuid4", "import random",
                 "random.", "import secrets", "secrets.", "os.environ", "getenv",
                 "urandom", "perf_counter", "import threading", "import asyncio",
                 "import socket", "import requests")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


# ── cross-process identity ───────────────────────────────────────────────────────

def test_cross_process_binding_hash():
    code = (
        "import runtime_binding as rb;"
        "raw=[{'event_type':'recurrence','entity_id':'margin_crisis:wildberries:SKU-a1b2c3d4e5f67890a1b2c3d4e5f67890','metadata_json':'{\"weight\": 6}','created_at':'2026-05-31T10:00:00','id':'evt-1','user_id':'u1'},"
        "{'event_type':'margin_pressure','entity_id':'margin_crisis:wildberries:A','metadata_json':'{\"weight\": 8, \"marketplace\": \"wildberries\"}','created_at':'2026-05-30','user_id':'u1'},"
        "{'event_type':'cascade','entity_id':'high_ad_spend:ozon:B-550e8400e29b41d4a716446655440000','metadata_json':None,'created_at':'2026-05-29'}];"
        "print(rb.build_runtime_binding(raw).runtime_binding_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == GOLDEN_BINDING_HASH
