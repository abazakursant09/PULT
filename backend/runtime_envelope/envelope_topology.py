"""Runtime layer topology (Sprint 73) — FROZEN.

Encodes the fixed position of the Runtime Envelope in the runtime stack:

    upstream_events
        -> runtime_signal_collector
        -> runtime_signal_set
        -> RUNTIME_ENVELOPE        (this layer)
        -> operator_cognition_v2
        -> operator_console

The topology is an immutable ordered structure. `topology_attestation()` is a
deterministic hash over it — any reordering or relabeling changes the attestation
and therefore the whole envelope hash.
"""
from __future__ import annotations

from .envelope_hash import domain_hash

# Ordered runtime layers. The envelope sits at index ENVELOPE_INDEX, strictly
# above the collector/signal-set and strictly below cognition_v2.
TOPOLOGY: tuple[str, ...] = (
    "upstream_events",
    "runtime_signal_collector",
    "runtime_signal_set",
    "runtime_envelope",
    "operator_cognition_v2",
    "operator_console",
)

ENVELOPE_INDEX = TOPOLOGY.index("runtime_envelope")

# Directed adjacency (each layer feeds the next). Frozen.
TOPOLOGY_EDGES: tuple[tuple[str, str], ...] = tuple(
    (TOPOLOGY[i], TOPOLOGY[i + 1]) for i in range(len(TOPOLOGY) - 1)
)


def topology_attestation() -> str:
    """Deterministic attestation of the runtime topology."""
    return domain_hash("PULT-TOPOLOGY/1", {
        "layers": list(TOPOLOGY),
        "edges": [list(e) for e in TOPOLOGY_EDGES],
        "envelope_index": ENVELOPE_INDEX,
    })
