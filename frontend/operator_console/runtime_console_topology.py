"""Console topology — deterministic structured aggregation of what the operator
sees, plus the canonical operator_console_hash. Read-only; descriptive only.
"""
from __future__ import annotations

from runtime_envelope.envelope_hash import domain_hash

OPERATOR_CONSOLE_DOMAIN = "PULT-OPERATOR-CONSOLE/1"


# ── Deterministic SVG primitives (integer coordinates, no random layout) ────────

def svg_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg_open(width: int, height: int, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{svg_escape(title)}">')


def svg_rect(x: int, y: int, w: int, h: int, cls: str) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="{cls}"/>'


def svg_text(x: int, y: int, text: str, cls: str = "label") -> str:
    return f'<text x="{x}" y="{y}" class="{cls}">{svg_escape(text)}</text>'


def svg_line(x1: int, y1: int, x2: int, y2: int, cls: str) -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{cls}"/>'


def svg_close() -> str:
    return "</svg>"


def build_console_topology(state) -> dict:
    """Structured (non-SVG) console topology — the data the views render."""
    app = state.application
    data = {
        "descriptive_only": True,
        "execution_authority": False,
        "mutation_authority": False,
        "runtime_application_hash": app.runtime_application_hash,
        "runtime_event_count": app.runtime_event_count,
        "state": app.state,
        "pressure": app.pressure,
        "interventions": app.interventions,
        "drift": app.drift,
        "regions": app.pressure["accumulation_regions"],
        "replay_identity": {
            "runtime_application_hash": app.runtime_application_hash,
            "event_count": app.replay["event_count"],
            "window_size": app.replay["window_size"],
            "window_count": len(app.replay["windows"]),
        },
        "view_counts": {
            "dashboard": 1,
            "pressure": len(app.pressure["accumulation_regions"]),
            "drift": len(app.drift["drift_regions"]),
            "intervention": len(app.interventions),
            "replay": len(app.replay["windows"]),
            "regions": len(app.pressure["accumulation_regions"]),
        },
    }
    data["operator_console_hash"] = domain_hash(OPERATOR_CONSOLE_DOMAIN, {
        k: v for k, v in data.items() if k != "operator_console_hash"
    })
    return data
