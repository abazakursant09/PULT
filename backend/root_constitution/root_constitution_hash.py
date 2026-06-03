"""Root Constitution hashing (Sprint 77).

Derives each of the seven constitutional anchors deterministically from frozen
repository state and fixed canonical inputs, then folds them — in canonical
order — into the single root_constitutional_hash.

Reuses the frozen Runtime Envelope canonical SHA-256. No timestamps, randomness,
uuid, env, network. File reads cover committed CONTENT only (never metadata).
"""
from __future__ import annotations

import sys
from pathlib import Path

from runtime_envelope.envelope_hash import sha256_hex, domain_hash
from runtime_envelope import build_runtime_envelope
from replay_chain import build_replay_chain
from runtime_application import build_runtime_application
from operational_review import build_review_session

from .root_constitution_contract import (
    ROOT_DOMAIN, CONSTITUTION_ORDER, CANONICAL_ENVELOPE_COMPONENTS, CANONICAL_EVENT_LOG,
)

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_FRONTEND = _REPO / "frontend"


# ── Anchor 1: schema baseline revision (alembic) ────────────────────────────────

def schema_baseline_revision() -> str:
    versions = sorted((_BACKEND / "alembic" / "versions").glob("*_baseline_full_schema.py"))
    if not versions:
        return "unknown"
    return versions[0].name.split("_", 1)[0]


# ── Anchor 2: logic characterization hash (Sprint 70-72 snapshots) ──────────────

def logic_characterization_hash() -> str:
    snaps = sorted((_BACKEND / "tests" / "characterization").glob("*/snapshot.json"),
                   key=lambda p: p.as_posix())
    items = [{"path": p.relative_to(_BACKEND).as_posix(),
              "sha256": sha256_hex(p.read_bytes())} for p in snaps]
    return sha256_hex(items)


# ── Anchor 3: runtime envelope hash (Sprint 73) ─────────────────────────────────

def runtime_envelope_hash() -> str:
    return build_runtime_envelope(**CANONICAL_ENVELOPE_COMPONENTS).runtime_envelope_hash


# ── Anchor 4: replay chain hash (Sprint 74) ─────────────────────────────────────

def replay_chain_hash() -> str:
    return build_replay_chain(CANONICAL_EVENT_LOG, schema_baseline_revision()).replay_chain_hash


# ── Anchor 5: runtime application hash (Sprint 75) ──────────────────────────────

def runtime_application_hash() -> str:
    return build_runtime_application(CANONICAL_EVENT_LOG).runtime_application_hash


# ── Anchor 6: operator console hash (Sprint 75 — frontend product layer) ────────

def operator_console_hash() -> str:
    if str(_FRONTEND) not in sys.path:
        sys.path.insert(0, str(_FRONTEND))
    from operator_console import build_console_topology, load_state
    return build_console_topology(load_state(CANONICAL_EVENT_LOG))["operator_console_hash"]


# ── Anchor 7: operational review hash (Sprint 76) ───────────────────────────────

def operational_review_hash() -> str:
    return build_review_session([("canonical", CANONICAL_EVENT_LOG)]).review_hash


# ── Anchor table + root fold ────────────────────────────────────────────────────

_ANCHOR_FN = {
    "schema_baseline_revision": schema_baseline_revision,
    "logic_characterization_hash": logic_characterization_hash,
    "runtime_envelope_hash": runtime_envelope_hash,
    "replay_chain_hash": replay_chain_hash,
    "runtime_application_hash": runtime_application_hash,
    "operator_console_hash": operator_console_hash,
    "operational_review_hash": operational_review_hash,
}


def compute_anchors() -> dict:
    """Compute every constitutional anchor (deterministic)."""
    return {name: _ANCHOR_FN[name]() for name in CONSTITUTION_ORDER}


def fold_root(anchors: dict) -> str:
    """Fold anchors into the root hash in canonical order (ordering attested)."""
    ordered_pairs = [[name, anchors[name]] for name in CONSTITUTION_ORDER]
    return domain_hash(ROOT_DOMAIN, {"order": list(CONSTITUTION_ORDER), "anchors": ordered_pairs})
