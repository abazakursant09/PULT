"""Characterization fixtures for logic.cross_mp_memory (Sprint 71). Observe-only.

Hand-built: build_memory_narrative needs a real CrossMarketplaceMemory (or None);
build_cross_mp_memory's non-recurrence branch uses datetime.utcnow() (would be
non-deterministic), so only deterministic paths are frozen — no-history (None),
unknown pattern (None), and the recurrence path (stability_days = fixed gap).
"""
from datetime import datetime

from logic.cross_mp_memory import build_cross_mp_memory, build_memory_narrative
from characterization._engine import call

# Two resolutions 45 days apart -> recurrence=True, stability_days=gap (deterministic).
_RH = {
    "margin_crisis:wildberries:A": datetime(2026, 1, 1),
    "high_ad_spend:wildberries:B": datetime(2026, 2, 15),
}


def build_cases():
    c = {}
    c["build_cross_mp_memory.no_history"] = call(build_cross_mp_memory, "multi_margin_pressure", {}, None)
    c["build_cross_mp_memory.unknown_pattern"] = call(build_cross_mp_memory, "margin_crisis", _RH, None)
    c["build_cross_mp_memory.recurrence"] = call(build_cross_mp_memory, "multi_margin_pressure", _RH, None)
    mem = build_cross_mp_memory("multi_margin_pressure", _RH, None)
    c["build_memory_narrative.none"] = call(build_memory_narrative, None)
    c["build_memory_narrative.recurrence"] = call(build_memory_narrative, mem)
    return c
