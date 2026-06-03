"""Characterization fixtures for logic.focus_engine (Sprint 71). Observe-only.

Hand-built: chains/scenarios must be empty lists (not insight objects);
focus_briefing_for_telegram needs a real OperationalFocus, produced by chaining
the real compute_operational_focus output.

DISCOVERED BUG (frozen, NOT fixed — Sprint 71 finding #1):
  compute_operational_focus() with a multi-signal chain (e.g. margin_crisis +
  high_ad_spend on the same product) returns an order-dependent `root_cause`
  (flips between the two categories across runs / hash seeds). To freeze
  DETERMINISTIC behavior, the populated fixture below uses a single dominant
  insight. The chain-tie nondeterminism is documented in
  docs/governance/sprint_71_logic_freeze.md and left unchanged.
"""
from logic.focus_engine import (
    compress_scenarios, compute_operational_focus, focus_briefing_for_telegram,
)
from characterization._engine import call, insight

_INSIGHTS = [
    insight(key="margin_crisis:wildberries:A", weight=70, signal_decay_state="fresh",
            title="Давление на маржу", product_name="AlphaMug",
            signal_lifecycle_stage="confirmed"),
]


def build_cases():
    c = {}
    c["compress_scenarios.empty"] = call(compress_scenarios, [])
    c["compute_operational_focus.empty"] = call(compute_operational_focus, [], [], [], {}, {}, 0.0)
    c["compute_operational_focus.populated"] = call(
        compute_operational_focus, _INSIGHTS, [], [], {}, {}, 0.0)

    focus = compute_operational_focus(_INSIGHTS, [], [], {}, {}, 0.0)
    c["focus_briefing_for_telegram.real"] = call(focus_briefing_for_telegram, focus)
    return c
