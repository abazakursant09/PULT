"""Constitutional verifier (Sprint 78).

verify_full_constitution() rebuilds the root constitution from the LIVE substrate
and confirms every layer anchor + the root hash reproduce the pinned
constitution-of-record, and that the root is internally consistent. Returns only
VALID or INVALID. Fail-closed: any mismatch -> INVALID.
"""
from __future__ import annotations

from root_constitution import build_root_constitution, verify_root_constitution
from root_constitution import VALID as _ROOT_VALID

from .constitution_contract import (
    VALID, INVALID, ENFORCED_LAYERS, EXPECTED_ANCHORS, EXPECTED_ROOT,
)


def _verify(root_obj, expected_anchors: dict, expected_root: str) -> str:
    for layer in ENFORCED_LAYERS:
        if getattr(root_obj, layer) != expected_anchors[layer]:
            return INVALID
    if root_obj.root_constitutional_hash != expected_root:
        return INVALID
    if verify_root_constitution(root_obj) != _ROOT_VALID:
        return INVALID
    return VALID


def verify_layers(root_obj) -> dict:
    """Per-layer verification map against the constitution-of-record."""
    return {layer: (getattr(root_obj, layer) == EXPECTED_ANCHORS[layer])
            for layer in ENFORCED_LAYERS}


def verify_full_constitution() -> str:
    """Verify the live substrate reproduces the pinned constitution. VALID/INVALID."""
    root_obj = build_root_constitution()
    return _verify(root_obj, EXPECTED_ANCHORS, EXPECTED_ROOT)
