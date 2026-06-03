"""Console state (read-only).

Holds the current Runtime Application built from a deterministic, append-only
event log. The console never ingests live events itself and never alters the
substrate — it reads a fixed log and renders. Same log -> same state.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime_application import build_runtime_application, console_view, RuntimeApplication

# Canonical deterministic event log used by the default console state.
DEFAULT_EVENT_LOG = (
    {"event_type": "margin_pressure", "entity": "margin_crisis:wildberries:A", "weight": 8},
    {"event_type": "ad_spend_spike", "entity": "high_ad_spend:wildberries:A", "weight": 7},
    {"event_type": "recurrence", "entity": "margin_crisis:wildberries:A", "weight": 6},
    {"event_type": "stock_drop", "entity": "low_stock:wildberries:B", "weight": 5},
    {"event_type": "insight_resolved", "entity": "high_ad_spend:wildberries:A", "weight": 3},
    {"event_type": "cascade", "entity": "margin_crisis:ozon:C", "weight": 6, "marketplace": "ozon"},
)


@dataclass(frozen=True)
class ConsoleState:
    application: RuntimeApplication
    view: dict


def load_state(event_log=DEFAULT_EVENT_LOG) -> ConsoleState:
    from runtime_application import ingest
    stream = ingest(event_log)
    return ConsoleState(
        application=build_runtime_application(event_log),
        view=console_view(stream),
    )


def default_state() -> ConsoleState:
    return load_state(DEFAULT_EVENT_LOG)
