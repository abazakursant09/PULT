"""Cognition binding adapter (Sprint 83) — the cognition-output bridge.

observe(insights) projects + canonicalizes cognition output and feeds it,
READ-ONLY, into the existing substrate (runtime_application, replay_chain,
operational_review) without modifying those layers or cognition. Produces a
deterministic CognitionBinding and records it in an append-only ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from runtime_application import build_runtime_application
from replay_chain import build_replay_chain
from operational_review import build_review_session

from .cognition_projection import project_insights
from .cognition_canonicalizer import canonicalize_projection, projection_to_events
from .cognition_binding_hash import cognition_binding_hash, ADAPTER_VERSION

BASELINE_ANCHOR = "47beea1df0c1"


@dataclass(frozen=True)
class CognitionBinding:
    adapter_version: str
    insight_count: int
    canonical_projection: tuple
    runtime_application_hash: str
    replay_chain_hash: str
    operational_review_hash: str
    cognition_binding_hash: str

    def summary(self) -> dict:
        return {
            "adapter_version": self.adapter_version,
            "insight_count": self.insight_count,
            "runtime_application_hash": self.runtime_application_hash,
            "replay_chain_hash": self.replay_chain_hash,
            "operational_review_hash": self.operational_review_hash,
            "cognition_binding_hash": self.cognition_binding_hash,
        }


def build_from_projection(canonical: tuple) -> CognitionBinding:
    events = projection_to_events(canonical)
    app = build_runtime_application(events)
    chain = build_replay_chain(events, BASELINE_ANCHOR)
    review = build_review_session([("cognition", events)])
    return CognitionBinding(
        adapter_version=ADAPTER_VERSION,
        insight_count=len(canonical),
        canonical_projection=canonical,
        runtime_application_hash=app.runtime_application_hash,
        replay_chain_hash=chain.replay_chain_hash,
        operational_review_hash=review.review_hash,
        cognition_binding_hash=cognition_binding_hash(canonical),
    )


def observe(insights) -> CognitionBinding:
    """Observe cognition output, canonicalize, bind to substrate. Fail-closed."""
    canonical = canonicalize_projection(project_insights(insights))
    return build_from_projection(canonical)


# ── append-only activation ledger (process-local; not part of any const. hash) ──

@dataclass
class _CognitionLedger:
    entries: list = field(default_factory=list)

    def record(self, h: str) -> int:
        seq = len(self.entries)
        self.entries.append((seq, h))
        return seq

    @property
    def count(self) -> int:
        return len(self.entries)

    def last(self):
        return self.entries[-1] if self.entries else None

    def reset(self) -> None:
        self.entries.clear()


COGNITION_LEDGER = _CognitionLedger()


def observe_and_record(insights):
    """Automatic hook for the live cognition loop. Returns the binding hash, or
    None if cognition output is not canonicalizable. Never raises into the loop."""
    try:
        binding = observe(insights)
    except Exception:
        return None
    COGNITION_LEDGER.record(binding.cognition_binding_hash)
    return binding.cognition_binding_hash
