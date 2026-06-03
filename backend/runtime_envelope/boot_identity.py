"""Boot identity (Sprint 73).

`boot_id` is a DETERMINISTIC function of the runtime's structural anchors — NOT a
generated/random/clock value. The same boot inputs always reproduce the same
boot_id, on any machine, in any process, at any time. This is what makes a boot
reproducible under replay.
"""
from __future__ import annotations

from .envelope_hash import domain_hash

BOOT_DOMAIN = "PULT-BOOT/1"


def compute_boot_id(
    *,
    baseline_anchor: str,
    collector_signature: str,
    cognition_v2_runtime_hash: str,
) -> str:
    """Reproducible boot identity.

    Bound to the schema baseline, the signal collector, and the frozen cognition
    runtime. No timestamp, no pid, no randomness participates.
    """
    return domain_hash(BOOT_DOMAIN, {
        "baseline_anchor": baseline_anchor,
        "collector_signature": collector_signature,
        "cognition_v2_runtime_hash": cognition_v2_runtime_hash,
    })
