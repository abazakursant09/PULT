"""Read-only console routes. GET only — no mutation endpoints, no execution.

Every route is deterministic and replay-reconstructable: it rebuilds the console
state from the fixed event log and renders. No background work, no push channels.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response, HTMLResponse

from .runtime_console_state import default_state
from .runtime_console_topology import build_console_topology
from .runtime_dashboard_renderer import render_dashboard
from .pressure_visualization_renderer import render_pressure
from .drift_visualization_renderer import render_drift
from .intervention_surface_renderer import render_interventions
from .replay_timeline_renderer import render_replay
from .runtime_region_renderer import render_regions

router = APIRouter()

_SVG = "image/svg+xml"


def _svg(body: str) -> Response:
    return Response(content=body, media_type=_SVG)


@router.get("/runtime/topology")
def runtime_topology() -> Response:
    payload = build_console_topology(default_state())
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return Response(content=body, media_type="application/json")


@router.get("/runtime/pressure")
def runtime_pressure() -> Response:
    return _svg(render_pressure(default_state()))


@router.get("/runtime/drift")
def runtime_drift() -> Response:
    return _svg(render_drift(default_state()))


@router.get("/runtime/interventions")
def runtime_interventions() -> Response:
    return _svg(render_interventions(default_state()))


@router.get("/runtime/replay")
def runtime_replay() -> Response:
    return _svg(render_replay(default_state()))


@router.get("/runtime/regions")
def runtime_regions() -> Response:
    return _svg(render_regions(default_state()))


@router.get("/runtime/dashboard")
def runtime_dashboard() -> HTMLResponse:
    return HTMLResponse(content=render_dashboard(default_state()))
