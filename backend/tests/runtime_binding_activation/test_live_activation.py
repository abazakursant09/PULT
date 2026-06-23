"""Live Binding Activation tests (Sprint 81).

Proves that a UserEvent entering the system automatically activates runtime
binding (no manual invocation), while every constitutional guarantee is
preserved. Read-only over the frozen substrate; only the events ingestion path +
runtime_binding were touched.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # noqa: F401 — register tables on Base.metadata
from routers import events as events_module

import runtime_binding as rb
import root_constitution as root
import constitutional_enforcement as ce

# ── In-memory DB + minimal app mounting ONLY the events router ──────────────────
_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
    connect_args={"check_same_thread": False})
_Session = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def _create_tables():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.get_event_loop().run_until_complete(_create_tables())


async def _override_db():
    async with _Session() as s:
        yield s


_app = FastAPI()
_app.include_router(events_module.router, prefix="/api")
_app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u-test")
_app.dependency_overrides[get_db] = _override_db
client = TestClient(_app)

GOLDEN_ROOT = "816632a3242098f6e545a813ab66ab6429948d316aefacb23f484d3e598f7b1d"


def _track(event_type, entity_id, metadata=None):
    return client.post("/api/events/track", json={
        "event_type": event_type, "event_scope": "test",
        "entity_id": entity_id, "metadata": metadata,
    })


# ── automatic activation via the ingestion path ─────────────────────────────────

def test_userevent_creation_activates_binding():
    rb.ACTIVATION_LEDGER.reset()
    r = _track("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert r.status_code == 204
    assert rb.activation_count() == 1            # produced WITHOUT manual invocation
    assert rb.last_binding_hash() is not None
    assert len(rb.last_binding_hash()) == 64


def test_activation_hash_matches_manual_binding():
    rb.ACTIVATION_LEDGER.reset()
    _track("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    expected = rb.bind_single_event("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert rb.last_binding_hash() == expected


def test_multiple_events_append_only_ledger():
    rb.ACTIVATION_LEDGER.reset()
    for i in range(5):
        _track("insight_opened", f"margin_crisis:wildberries:A{i}", {"weight": i})
    assert rb.activation_count() == 5
    assert [seq for seq, _ in rb.ACTIVATION_LEDGER.entries] == [0, 1, 2, 3, 4]


def test_disallowed_event_does_not_activate():
    rb.ACTIVATION_LEDGER.reset()
    r = _track("not_an_allowed_event", "margin_crisis:wildberries:A", {"weight": 5})
    assert r.status_code == 204
    assert rb.activation_count() == 0            # early-returned before binding


def test_entityless_event_fails_closed_no_binding():
    rb.ACTIVATION_LEDGER.reset()
    r = _track("insight_opened", None, {"weight": 5})
    assert r.status_code == 204                   # ingestion preserved
    assert rb.activation_count() == 0             # fail-closed: no binding produced


def test_ingestion_still_returns_204_even_if_binding_skipped():
    rb.ACTIVATION_LEDGER.reset()
    assert _track("insight_opened", None, None).status_code == 204


# ── determinism (same logical event -> same activation hash) ────────────────────

@pytest.mark.parametrize("i", list(range(20)))
def test_activation_deterministic(i):
    h1 = rb.bind_single_event("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    h2 = rb.bind_single_event("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert h1 == h2


def test_activation_strips_uuid_and_timestamp_irrelevant():
    # metadata carries no timestamp; entity uuid stripped -> same as clean entity
    h_uuid = rb.bind_single_event("insight_opened",
                                  "margin_crisis:wildberries:A-550e8400e29b41d4a716446655440000", {"weight": 5})
    h_clean = rb.bind_single_event("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert h_uuid == h_clean


# ── activation hook fail-closed (never raises) ──────────────────────────────────

@pytest.mark.parametrize("bad_entity", [None, "", "550e8400e29b41d4a716446655440000"])
def test_activate_from_track_returns_none_on_bad(bad_entity):
    assert rb.activate_from_track("insight_opened", bad_entity, {"weight": 5}) is None


def test_activate_from_track_never_raises():
    # bad metadata type still must not raise out of the hook
    assert rb.activate_from_track("insight_opened", "margin_crisis:wb:A", {"weight": "bad"}) is None


# ── constitutional / substrate protection (preserved after activation) ──────────

def test_constitution_valid_after_activation():
    rb.ACTIVATION_LEDGER.reset()
    _track("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert ce.verify_full_constitution() == ce.VALID


def test_root_hash_unchanged_after_activation():
    rb.ACTIVATION_LEDGER.reset()
    _track("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert root.build_root_constitution().root_constitutional_hash == GOLDEN_ROOT


@pytest.mark.parametrize("layer", list(ce.ENFORCED_LAYERS))
def test_each_anchor_unchanged_after_activation(layer):
    rb.ACTIVATION_LEDGER.reset()
    _track("insight_opened", "margin_crisis:wildberries:A", {"weight": 5})
    assert getattr(root.build_root_constitution(), layer) == ce.EXPECTED_ANCHORS[layer]


# ── wiring proof (ingestion path actually calls the hook) ───────────────────────

def test_events_path_wires_activation():
    src = Path(events_module.__file__).read_text(encoding="utf-8")
    assert "activate_from_track" in src
    assert "from runtime_binding import activate_from_track" in src
