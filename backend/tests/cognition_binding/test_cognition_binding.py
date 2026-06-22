"""Cognition Output Binding tests (Sprint 83).

Categories: deterministic hash, replay reconstruction, uuid stripping, timestamp
stripping, ordering normalization, cross-process identity, tamper detection,
canonicalization stability, constitution preservation, substrate protection,
fail-closed, no forbidden imports, no mutation authority.

Read-only over the frozen substrate; only cognition_binding + the live loop wire
were added.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import cognition_binding as cb
import root_constitution as root
import constitutional_enforcement as ce

_BACKEND = Path(__file__).resolve().parents[2]


def _ins(**kw):
    base = dict(
        id="uuid-a1b2c3d4e5f67890", record_id="rec-uuid", chain_id="ch-uuid",
        key="margin_crisis:wildberries:SKU-550e8400e29b41d4a716446655440000",
        type="warning", status="active", confidence=70, confidence_level="high",
        impact_score=50, marketplace="wildberries", is_demo=False, is_secondary=False,
        signal_state="persistent", resolution_difficulty="hard",
        intervention_tier="attention", automation_level=None,
        reasons=["low CTR"], recommendations=["check price"],
        marketplace_patterns=["p2", "p1"],
    )
    base.update(kw)
    return NS(**base)


def _fixture():
    return [_ins(), _ins(key="high_ad_spend:ozon:B", marketplace="ozon", impact_score=30, type="info")]


GOLDEN_HASH = "f08841515f3e5701f920b8a5e99802d792ef704b58be331cb9db0fb1e3f5fdd3"
GOLDEN_ROOT = "816632a3242098f6e545a813ab66ab6429948d316aefacb23f484d3e598f7b1d"
GOLDEN_ENVELOPE = "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4"


# ── deterministic hash ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("i", list(range(95)))
def test_hash_stable(i):
    assert cb.observe(_fixture()).cognition_binding_hash == GOLDEN_HASH


@pytest.mark.parametrize("i", list(range(20)))
def test_replay_reconstruction_repeat(i):
    b = cb.observe(_fixture())
    assert cb.build_from_projection(b.canonical_projection).cognition_binding_hash == GOLDEN_HASH


def test_hash_64_hex():
    h = cb.observe(_fixture()).cognition_binding_hash
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ── ordering normalization ───────────────────────────────────────────────────────

_PERMS = [[0, 1], [1, 0]]


@pytest.mark.parametrize("perm", _PERMS)
def test_order_invariant(perm):
    f = _fixture()
    permuted = [f[i] for i in perm]
    assert cb.observe(permuted).cognition_binding_hash == GOLDEN_HASH


def test_marketplace_patterns_sorted():
    proj = cb.observe(_fixture()).canonical_projection
    for rec in proj:
        assert rec["marketplace_patterns"] == sorted(rec["marketplace_patterns"])


def test_projection_sorted():
    proj = cb.observe(_fixture()).canonical_projection
    keys = [(r["category"], r["entity"], r["type"], r["status"]) for r in proj]
    assert keys == sorted(keys)


# ── uuid stripping ───────────────────────────────────────────────────────────────

_UUID_KEYS = [
    "margin_crisis:wildberries:SKU-550e8400e29b41d4a716446655440000",
    "margin_crisis:wildberries:SKU-ffffffffffffffffffffffffffffffff",
    "margin_crisis:wildberries:SKU-00000000-0000-0000-0000-000000000000",
]


@pytest.mark.parametrize("key", _UUID_KEYS)
def test_uuid_in_key_stripped_to_same_hash(key):
    f = [_ins(key=key), _ins(key="high_ad_spend:ozon:B", marketplace="ozon", impact_score=30, type="info")]
    assert cb.observe(f).cognition_binding_hash == GOLDEN_HASH


@pytest.mark.parametrize("idval", ["uuid-x", "uuid-y", "different-id", None])
def test_id_fields_dropped(idval):
    f = [_ins(id=idval, record_id=idval, chain_id=idval),
         _ins(key="high_ad_spend:ozon:B", marketplace="ozon", impact_score=30, type="info")]
    assert cb.observe(f).cognition_binding_hash == GOLDEN_HASH


# ── timestamp / temporal-metadata stripping ──────────────────────────────────────

@pytest.mark.parametrize("ts", ["2026-01-01", "2020-12-31T23:59:59", "x", None, ""])
def test_extra_temporal_field_ignored(ts):
    f = [_ins(created_at=ts, updated_at=ts), _ins(key="high_ad_spend:ozon:B", marketplace="ozon", impact_score=30, type="info")]
    assert cb.observe(f).cognition_binding_hash == GOLDEN_HASH


# ── canonicalization stability ───────────────────────────────────────────────────

def test_canonical_entity_uuid_free():
    proj = cb.observe(_fixture()).canonical_projection
    for rec in proj:
        assert "550e8400" not in rec["entity"]


def test_canonicalization_idempotent():
    proj = cb.project_insights(_fixture())
    a = cb.canonicalize_projection(proj)
    b = cb.canonicalize_projection(list(reversed(proj)))
    assert a == b


def test_projection_to_events_shape():
    canonical = cb.canonicalize_projection(cb.project_insights(_fixture()))
    for ev in cb.projection_to_events(canonical):
        assert set(ev) == {"event_type", "entity", "weight", "marketplace"}
        assert isinstance(ev["weight"], int)


# ── replay reconstruction ────────────────────────────────────────────────────────

def test_replay_reconstruction_equal():
    assert cb.observe(_fixture()) == cb.observe(_fixture())


def test_build_from_projection_matches_observe():
    b = cb.observe(_fixture())
    assert cb.build_from_projection(b.canonical_projection) == b


@pytest.mark.parametrize("i", list(range(10)))
def test_attestation_valid(i):
    assert cb.verify_binding(cb.attest(_fixture())) == cb.VALID


# ── tamper detection ─────────────────────────────────────────────────────────────

def test_tamper_projection_invalid():
    att = cb.attest(_fixture())
    bad = dataclasses.replace(att, canonical_projection=())
    assert cb.verify_binding(bad) == cb.INVALID


def test_tamper_binding_hash_invalid():
    att = cb.attest(_fixture())
    bad_b = dataclasses.replace(att.binding, cognition_binding_hash="0" * 64)
    assert cb.verify_binding(dataclasses.replace(att, binding=bad_b)) == cb.INVALID


def test_tamper_app_hash_invalid():
    att = cb.attest(_fixture())
    bad_b = dataclasses.replace(att.binding, runtime_application_hash="0" * 64)
    assert cb.verify_binding(dataclasses.replace(att, binding=bad_b)) == cb.INVALID


def test_tamper_replay_hash_invalid():
    att = cb.attest(_fixture())
    bad_b = dataclasses.replace(att.binding, replay_chain_hash="0" * 64)
    assert cb.verify_binding(dataclasses.replace(att, binding=bad_b)) == cb.INVALID


def test_tamper_review_hash_invalid():
    att = cb.attest(_fixture())
    bad_b = dataclasses.replace(att.binding, operational_review_hash="0" * 64)
    assert cb.verify_binding(dataclasses.replace(att, binding=bad_b)) == cb.INVALID


def test_verify_only_valid_or_invalid():
    assert cb.verify_binding(cb.attest(_fixture())) in (cb.VALID, cb.INVALID)


# ── fail-closed ──────────────────────────────────────────────────────────────────

def test_fail_closed_missing_key():
    with pytest.raises(cb.CognitionBindingViolation):
        cb.observe([_ins(key=None)])


def test_fail_closed_empty_key():
    with pytest.raises(cb.CognitionBindingViolation):
        cb.observe([_ins(key="")])


def test_fail_closed_entity_all_uuid():
    with pytest.raises(cb.CognitionBindingViolation):
        cb.observe([_ins(key="550e8400e29b41d4a716446655440000")])


def test_observe_and_record_returns_none_on_bad():
    cb.COGNITION_LEDGER.reset()
    assert cb.observe_and_record([_ins(key=None)]) is None
    assert cb.COGNITION_LEDGER.count == 0


# ── observe_and_record (loop hook) ──────────────────────────────────────────────

def test_observe_and_record_appends():
    cb.COGNITION_LEDGER.reset()
    h = cb.observe_and_record(_fixture())
    assert h == GOLDEN_HASH
    assert cb.COGNITION_LEDGER.count == 1


def test_observe_and_record_append_only():
    cb.COGNITION_LEDGER.reset()
    for _ in range(4):
        cb.observe_and_record(_fixture())
    assert [s for s, _ in cb.COGNITION_LEDGER.entries] == [0, 1, 2, 3]


def test_loop_wires_cognition_binding():
    src = (_BACKEND / "tasks" / "intelligence_loop.py").read_text(encoding="utf-8")
    assert "observe_and_record" in src
    assert "from cognition_binding import observe_and_record" in src


# ── constitution / substrate protection ──────────────────────────────────────────

def test_constitution_valid_after_observe():
    cb.observe(_fixture())
    assert ce.verify_full_constitution() == ce.VALID


def test_root_unchanged_after_observe():
    cb.observe(_fixture())
    assert root.build_root_constitution().root_constitutional_hash == GOLDEN_ROOT


def test_envelope_unchanged_after_observe():
    cb.observe(_fixture())
    assert root.runtime_envelope_hash() == GOLDEN_ENVELOPE


@pytest.mark.parametrize("layer", list(ce.ENFORCED_LAYERS))
def test_each_anchor_unchanged(layer):
    cb.observe(_fixture())
    assert getattr(root.build_root_constitution(), layer) == ce.EXPECTED_ANCHORS[layer]


# ── boundary / immutability / no mutation authority ─────────────────────────────

def test_boundary_flags():
    assert cb.EXECUTION_AUTHORITY is False
    assert cb.MUTATION_AUTHORITY is False
    assert cb.RECOMMENDATION_AUTHORITY is False
    assert cb.PREDICTION_AUTHORITY is False
    assert cb.READ_ONLY is True
    assert cb.FAIL_CLOSED is True
    assert cb.DETERMINISTIC is True


def test_binding_is_frozen():
    b = cb.observe(_fixture())
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.cognition_binding_hash = "x"  # type: ignore[misc]


def test_observe_does_not_mutate_insights():
    f = _fixture()
    before = (f[0].key, f[0].confidence, list(f[0].marketplace_patterns))
    cb.observe(f)
    after = (f[0].key, f[0].confidence, list(f[0].marketplace_patterns))
    assert before == after


# ── no forbidden imports ─────────────────────────────────────────────────────────

def test_no_forbidden_imports():
    pkg = Path(cb.__file__).resolve().parent
    forbidden = ("import time", "time.time", "import datetime", "from datetime",
                 "datetime.", "import uuid", "uuid.", "uuid4", "import random",
                 "random.", "import secrets", "os.environ", "getenv", "urandom",
                 "perf_counter", "import threading", "import asyncio", "import socket")
    offenders = []
    for src in pkg.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in text:
                offenders.append(f"{src.name}: {tok}")
    assert not offenders, offenders


# ── cross-process identity ───────────────────────────────────────────────────────

def test_cross_process_identity():
    code = (
        "import cognition_binding as cb;"
        "from types import SimpleNamespace as NS;"
        "base=dict(id='uuid-a1b2c3d4e5f67890',record_id='rec-uuid',chain_id='ch-uuid',"
        "key='margin_crisis:wildberries:SKU-550e8400e29b41d4a716446655440000',type='warning',status='active',"
        "confidence=70,confidence_level='high',impact_score=50,marketplace='wildberries',is_demo=False,"
        "is_secondary=False,signal_state='persistent',resolution_difficulty='hard',intervention_tier='attention',"
        "automation_level=None,reasons=['low CTR'],recommendations=['check price'],marketplace_patterns=['p2','p1']);"
        "a=NS(**base);b2=dict(base);b2.update(key='high_ad_spend:ozon:B',marketplace='ozon',impact_score=30,type='info');"
        "b=NS(**b2);print(cb.observe([a,b]).cognition_binding_hash)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_BACKEND),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == GOLDEN_HASH
