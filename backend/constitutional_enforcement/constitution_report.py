"""Deterministic constitution report (Sprint 78).

Produces a deterministic report: status, verified layers, root hash, and a
verification_signature (a hash binding the report content). No timestamps, no
randomness, no mutable metadata.
"""
from __future__ import annotations

from runtime_envelope.envelope_hash import domain_hash
from root_constitution import build_root_constitution

from .constitution_contract import (
    ConstitutionReport, ENFORCEMENT_DOMAIN, ENFORCED_LAYERS,
)
from .constitution_verifier import _verify, verify_layers
from .constitution_contract import EXPECTED_ANCHORS, EXPECTED_ROOT


def build_constitution_report() -> ConstitutionReport:
    root_obj = build_root_constitution()
    status = _verify(root_obj, EXPECTED_ANCHORS, EXPECTED_ROOT)
    layer_map = verify_layers(root_obj)
    verified_layers = tuple(layer for layer in ENFORCED_LAYERS if layer_map[layer])

    signature = domain_hash(ENFORCEMENT_DOMAIN, {
        "constitutional_status": status,
        "verified_layers": list(verified_layers),
        "root_constitutional_hash": root_obj.root_constitutional_hash,
    })
    return ConstitutionReport(
        constitutional_status=status,
        verified_layers=verified_layers,
        root_constitutional_hash=root_obj.root_constitutional_hash,
        verification_signature=signature,
    )
