"""Constitutional Enforcement contract (Sprint 78) — FROZEN.

Pins the constitution-of-record: the seven layer anchors and the root hash that
the live substrate MUST reproduce. Any divergence is a violation.
"""
from __future__ import annotations

from dataclasses import dataclass

ENFORCEMENT_DOMAIN = "PULT-CONSTITUTIONAL-ENFORCEMENT/1"

VALID = "VALID"
INVALID = "INVALID"

# Layers verified, in canonical order (matches root_constitution.CONSTITUTION_ORDER).
ENFORCED_LAYERS: tuple[str, ...] = (
    "schema_baseline_revision",
    "logic_characterization_hash",
    "runtime_envelope_hash",
    "replay_chain_hash",
    "runtime_application_hash",
    "operator_console_hash",
    "operational_review_hash",
)

# Constitution-of-record (Sprint 77 goldens). The live substrate must reproduce these.
EXPECTED_ANCHORS: dict = {
    "schema_baseline_revision": "47beea1df0c1",
    "logic_characterization_hash": "51f8968c909c7c6939e6e2f0a5e15e23cfbd82e703d6ab8cf90adc0db401c354",
    "runtime_envelope_hash": "77fc5a023483a2f1068296856df1cf5801b67e7977fcdac16db21fa508ab2cb4",
    "replay_chain_hash": "9ea8fa658eb0c9ea85c80bd5fd33fa2103e5a948863b3c742cd59b361151926e",
    "runtime_application_hash": "af8700dd9fa80ceb6ea98de78e2d7893ca4977a0a7fe87db02df5ee64e158d88",
    "operator_console_hash": "9d26fd652a21449fc018a7fb5266bf5bbbb8d9e5e4eeb1c4780a1802694f370a",
    "operational_review_hash": "8372703672d25cc8a1ff424ffdfe257bff64ba30972627699180f9534186e999",
}

EXPECTED_ROOT = "ae931b5dbebfa11d72bb0a33ea0e9fa6f8442098b7341a1eb0527b54bf85b69a"


@dataclass(frozen=True)
class ConstitutionReport:
    constitutional_status: str
    verified_layers: tuple
    root_constitutional_hash: str
    verification_signature: str

    def to_dict(self) -> dict:
        return {
            "constitutional_status": self.constitutional_status,
            "verified_layers": list(self.verified_layers),
            "root_constitutional_hash": self.root_constitutional_hash,
            "verification_signature": self.verification_signature,
        }
