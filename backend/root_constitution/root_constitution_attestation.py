"""Root Constitution attestation (Sprint 77).

build_root_constitution()  — derive all anchors and seal the root hash.
verify_root_constitution() — fully reconstruct the root hash and return
                             "VALID" or "INVALID". Nothing else.
"""
from __future__ import annotations

from .root_constitution_contract import RootConstitution, CONSTITUTION_ORDER
from .root_constitution_hash import compute_anchors, fold_root

VALID = "VALID"
INVALID = "INVALID"


def build_root_constitution() -> RootConstitution:
    anchors = compute_anchors()
    root = fold_root(anchors)
    return RootConstitution(
        schema_baseline_revision=anchors["schema_baseline_revision"],
        logic_characterization_hash=anchors["logic_characterization_hash"],
        runtime_envelope_hash=anchors["runtime_envelope_hash"],
        replay_chain_hash=anchors["replay_chain_hash"],
        runtime_application_hash=anchors["runtime_application_hash"],
        operator_console_hash=anchors["operator_console_hash"],
        operational_review_hash=anchors["operational_review_hash"],
        root_constitutional_hash=root,
    )


def verify_root_constitution(root: RootConstitution) -> str:
    """Reconstruct the root hash from the constitution's own anchors and confirm
    it matches byte-for-byte. Returns VALID or INVALID."""
    anchors = {name: getattr(root, name) for name in CONSTITUTION_ORDER}
    return VALID if fold_root(anchors) == root.root_constitutional_hash else INVALID
