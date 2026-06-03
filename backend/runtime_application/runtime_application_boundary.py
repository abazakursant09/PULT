"""Runtime Application boundary (Post-closure directive).

Declares the authority boundary of the Operational Runtime Application Layer.
This layer is STRICTLY descriptive: it observes the frozen substrate and renders
deterministic projections. It holds no authority to execute or alter anything.

All upstream layers (replay_chain, runtime_envelope, logic, schema) are treated
as READ-ONLY. This module is the single source of those flags.
"""
from __future__ import annotations

# Authority boundary — frozen.
EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
DESCRIPTIVE_ONLY = True
FAIL_CLOSED = True
REPLAY_COMPATIBLE = True
DETERMINISTIC = True


class BoundaryViolation(Exception):
    """Raised when an operation would exceed descriptive-only authority."""


def assert_descriptive_only() -> None:
    if EXECUTION_AUTHORITY or MUTATION_AUTHORITY or not DESCRIPTIVE_ONLY:
        raise BoundaryViolation("runtime_application is descriptive-only")


def fail_closed(reason: str) -> "BoundaryViolation":
    """Return a fail-closed violation (caller raises). Default posture is closed:
    on any malformed or out-of-scope input the layer refuses rather than guesses."""
    return BoundaryViolation(f"fail-closed: {reason}")
