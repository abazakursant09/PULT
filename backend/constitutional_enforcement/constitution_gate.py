"""Constitution gate (Sprint 78) — CI enforcement entrypoint.

Runs full constitutional verification and exits non-zero on any violation, so a
violation fails the pipeline before merge. No exceptions.

    python -m constitutional_enforcement.constitution_gate
"""
from __future__ import annotations

import sys

from .constitution_verifier import verify_full_constitution
from .constitution_report import build_constitution_report
from .constitution_contract import VALID


def gate() -> int:
    """Return 0 if the constitution is VALID, 1 otherwise (fail-closed)."""
    report = build_constitution_report()
    status = verify_full_constitution()
    print(f"constitutional_status={status}")
    print(f"root_constitutional_hash={report.root_constitutional_hash}")
    print(f"verification_signature={report.verification_signature}")
    print(f"verified_layers={len(report.verified_layers)}/7")
    return 0 if status == VALID else 1


def main() -> None:
    sys.exit(gate())


if __name__ == "__main__":
    main()
