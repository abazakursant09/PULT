"""
Product Graph API + reader — read-only contract (Doctrine §3/§7).

Self-contained: своя in-memory sqlite, без главного app/auth/fixtures.
HTTP-слой проверяется на минимальном FastAPI с примонтированным ТОЛЬКО
product_graph.router и override get_db / get_current_user.

Покрытие:
  reader:
    * дерево атом→листинги→решения собрано верно
    * listing_count / marketplaces / needs_review производные верны
    * summary-счётчики (atoms/listings/decisions/unconfirmed) сходятся с деревом
    * scope: чужие атомы/решения не видны
    * листинги отсортированы детерминированно (marketplace, external_id)
    * decision с physical_product_id=None не падает в дерево
    * пустой граф → нули, не ошибка
    * get_atom: свой → объект, чужой/несуществующий → None
  http:
    * GET ""         200 + форма ProductGraph
    * GET "/summary" 200 + счётчики
    * GET "/{id}"    200 свой / 404 чужой
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 — register mappers
from database import Base, get_db
from dependencies import get_current_user
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.decision import Decision
from services.product_graph import get_product_graph, get_atom
from routers import product_graph as pg_router

USER = "u-graph"
OTHER = "u-other"


# ── in-memory engine shared by reader + http tests ───────────────────────────────
_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
    connect_args={"check_same_thread": False})
_Session = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed():
    async with _engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with _Session() as db:
        # атом A: 2 листинга (wb+ozon), 1 подтверждён barcode, 1 fuzzy unconfirmed, + decision
        db.add(PhysicalProduct(id="A", user_id=USER, title="Кружка", barcode="111",
                               seller_sku="MUG", trademark_status="unknown"))
        db.add(ProductListing(id="A-wb", physical_product_id="A", user_id=USER,
                              marketplace="wb", external_id="WB1", title="Кружка",
                              match_method="seed", match_confidence=1.0, confirmed=True))
        db.add(ProductListing(id="A-oz", physical_product_id="A", user_id=USER,
                              marketplace="ozon", external_id="OZ1", title="Кружка чёрная",
                              match_method="name_fuzzy", match_confidence=0.85, confirmed=False))
        db.add(Decision(id="D1", user_id=USER, physical_product_id="A", listing_id="A-oz",
                        problem="Контент расходится", severity="warn", status="open",
                        action="Выровнять", pnl_impact=1500.0, pnl_level="level1"))
        # атом B: 1 листинг, без решений
        db.add(PhysicalProduct(id="B", user_id=USER, title="Лампа",
                               trademark_status="unknown"))
        db.add(ProductListing(id="B-wb", physical_product_id="B", user_id=USER,
                              marketplace="wb", external_id="WB2", title="Лампа",
                              match_method="seed", match_confidence=1.0, confirmed=True))
        # decision без атома — НЕ должен попасть в дерево
        db.add(Decision(id="D-orphan", user_id=USER, physical_product_id=None,
                        listing_id=None, problem="ничей", severity="warn", status="open"))
        # чужой атом — scope не должен его отдать
        db.add(PhysicalProduct(id="X", user_id=OTHER, title="Чужое",
                               trademark_status="unknown"))
        db.add(ProductListing(id="X-wb", physical_product_id="X", user_id=OTHER,
                              marketplace="wb", external_id="WBX", confirmed=True))
        await db.commit()


# Module-owned event loop: never rely on the global asyncio policy loop, which a
# prior async suite may have closed — under Python 3.13 asyncio.get_event_loop()
# then raises "There is no current event loop in thread 'MainThread'". Seed and
# every _run share this one loop so the aiosqlite StaticPool connection keeps its
# single-loop affinity.
_LOOP = asyncio.new_event_loop()
_LOOP.run_until_complete(_seed())


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _graph(user):
    async with _Session() as db:
        return await get_product_graph(user, db)


# ── reader ───────────────────────────────────────────────────────────────────────

def test_tree_shape():
    g = _run(_graph(USER))
    ids = [a["id"] for a in g["atoms"]]
    assert set(ids) == {"A", "B"}
    a = next(x for x in g["atoms"] if x["id"] == "A")
    assert len(a["listings"]) == 2
    assert len(a["decisions"]) == 1
    assert a["decisions"][0]["listing_id"] == "A-oz"


def test_derived_fields():
    g = _run(_graph(USER))
    a = next(x for x in g["atoms"] if x["id"] == "A")
    assert a["listing_count"] == 2
    assert a["marketplaces"] == ["ozon", "wb"]
    assert a["needs_review"] is True          # A-oz unconfirmed
    b = next(x for x in g["atoms"] if x["id"] == "B")
    assert b["needs_review"] is False


def test_summary_matches_tree():
    g = _run(_graph(USER))
    s = g["summary"]
    assert s["atoms"] == 2
    assert s["listings"] == 3
    assert s["decisions"] == 1                # orphan decision excluded
    assert s["unconfirmed_listings"] == 1
    assert s["marketplaces"] == ["ozon", "wb"]


def test_scope_isolation():
    g = _run(_graph(USER))
    assert all(a["id"] != "X" for a in g["atoms"])
    go = _run(_graph(OTHER))
    assert [a["id"] for a in go["atoms"]] == ["X"]
    assert go["summary"]["decisions"] == 0


def test_listing_order_deterministic():
    a = next(x for x in _run(_graph(USER))["atoms"] if x["id"] == "A")
    mps = [(l["marketplace"], l["external_id"]) for l in a["listings"]]
    assert mps == sorted(mps)


def test_empty_graph():
    g = _run(_graph("nobody"))
    assert g["atoms"] == []
    assert g["summary"] == {"atoms": 0, "listings": 0, "decisions": 0,
                            "unconfirmed_listings": 0, "marketplaces": []}


def test_get_atom_scope():
    own = _run(_atom(USER, "A"))
    assert own is not None and own["id"] == "A"
    assert _run(_atom(USER, "X")) is None       # чужой
    assert _run(_atom(USER, "nope")) is None     # несуществующий


async def _atom(user, pid):
    async with _Session() as db:
        return await get_atom(user, pid, db)


# ── http ─────────────────────────────────────────────────────────────────────────

async def _override_db():
    async with _Session() as s:
        yield s


def _client(user):
    app = FastAPI()
    app.include_router(pg_router.router, prefix="/api/product-graph")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_http_full_graph():
    r = _client(USER).get("/api/product-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["atoms"] == 2
    assert {a["id"] for a in body["atoms"]} == {"A", "B"}


def test_http_summary():
    r = _client(USER).get("/api/product-graph/summary")
    assert r.status_code == 200
    assert r.json()["unconfirmed_listings"] == 1


def test_http_atom_200_and_404():
    c = _client(USER)
    assert c.get("/api/product-graph/A").status_code == 200
    assert c.get("/api/product-graph/X").status_code == 404   # чужой → 404
