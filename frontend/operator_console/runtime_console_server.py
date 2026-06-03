"""Operator Console server — read-only FastAPI app.

No background workers, no push channels, no task queues, no lifespan tasks. Only
deterministic read-only GET routes over the Runtime Application topology.
"""
from __future__ import annotations

from fastapi import FastAPI

from .runtime_console_routes import router

app = FastAPI(
    title="PULT Operator Console",
    description="Read-only deterministic view over Runtime Application v1",
    version="1.0.0",
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "descriptive_only": True, "execution_authority": False}
