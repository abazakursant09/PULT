"""Binding adapter (Sprint 80) — the runtime-to-constitution bridge.

Canonicalizes the live UserEvent stream and feeds it, READ-ONLY, into the
existing substrate (runtime_application, replay_chain, operational_review)
without modifying any of those layers. Produces a deterministic RuntimeBinding.
"""
from __future__ import annotations

from runtime_application import build_runtime_application
from replay_chain import build_replay_chain
from operational_review import build_review_session

from .event_canonicalizer import canonicalize_stream
from .binding_hash import runtime_binding_hash
from .runtime_binding_contract import RuntimeBinding, ADAPTER_VERSION, BASELINE_ANCHOR


def build_runtime_binding(raw_events) -> RuntimeBinding:
    """Bridge a raw UserEvent stream to the constitutional substrate. Fail-closed."""
    canonical = canonicalize_stream(raw_events)

    # Feed the existing substrate read-only (no layer behavior changed).
    app = build_runtime_application(canonical)
    chain = build_replay_chain(canonical, BASELINE_ANCHOR)
    review = build_review_session([("runtime_binding", canonical)])

    return RuntimeBinding(
        adapter_version=ADAPTER_VERSION,
        canonical_event_count=len(canonical),
        canonical_events=canonical,
        runtime_application_hash=app.runtime_application_hash,
        replay_chain_hash=chain.replay_chain_hash,
        operational_review_hash=review.review_hash,
        runtime_binding_hash=runtime_binding_hash(canonical),
    )
