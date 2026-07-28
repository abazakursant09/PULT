"""PULT-LAUNCH-2.5E-1 — change-only writers + evidence fingerprint (feature OFF).

Two axes: (1) the pure evidence_fingerprint contract (canonical, versioned, order-independent, Decimal
scale, NULL≠0, ignores technical fields); (2) the change-only write behaviour of observe_price /
observe_promotion against an in-memory DB (first insert, unchanged no-row, the 24h last_verified rule,
a real change → a new version, the 100→90→100 three-change-point history, a NULL fingerprint forcing a
fresh insert, and the Yandex parent+child atomic write). Plus the schema guards (index composition,
nullability, SHA length) and the PostgreSQL EXPLAIN gate (BLOCKED_ENVIRONMENT when no PG is available —
SQLite EXPLAIN is never accepted as a substitute).

FK enforcement is intentionally left OFF here (default aiosqlite): these tests exercise the change-only
LOGIC with unassigned rows (product_id IS NULL), not tenant isolation, which its own suites cover.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 — register full metadata
from database import Base
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as PO, MarketplacePromotionStoreEvidence as SE)
from services.marketplace.ingest.change_only import (
    FINGERPRINT_VERSION, evidence_fingerprint, observe_price, observe_promotion,
    price_fingerprint, promo_fingerprint)

T0 = datetime(2026, 7, 28, 12, 0, 0)
_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── fingerprint payload helpers ─────────────────────────────────────────────────
def _pf(**over) -> dict:
    base = dict(promotion_id=None, promotion_type=None, participation_status=None,
                catalog_price=Decimal("2000"), buyer_price=Decimal("1500"), seller_promo_price=None,
                marketplace_subsidy=None, expected_seller_revenue=None, commission_base=None,
                provider_min_price=None, auto_action_enabled=None, club_buyer_price=None,
                currency="RUB", currency_status="proven", seller_revenue_status="unknown",
                commission_base_status="unknown", subsidy_status="unknown",
                provider_valid_from=None, provider_valid_to=None,
                provider_dataset="prices", missing_fields=[])
    base.update(over)
    return base


def _promf(**over) -> dict:
    base = dict(provider_status="PARTIALLY_AUTO", participation_status="active",
                auto_participation=True, attribution_status="exact_stores",
                pre_promo_price=Decimal("2000"), promo_buyer_price=Decimal("1500"),
                promo_max_price=None, currency="RUB", currency_status="proven",
                promotion_start_at=None, promotion_end_at=None, missing_fields=[])
    base.update(over)
    return base


async def _obs_price(db, now, **over):
    await observe_price(db, run_id=str(uuid.uuid4()), account_id="a", store_id="s", ext="E",
                        observation_kind="catalog", promotion_key="__none__", product_id=None,
                        source="api", now=now, fields=_pf(**over))
    await db.commit()


async def _obs_promo(db, now, child_evidence, **over):
    await observe_promotion(db, run_id=str(uuid.uuid4()), account_id="a", offer_id="OF", promo_id="PR",
                            product_id=None, now=now, fields=_promf(**over), child_evidence=child_evidence)
    await db.commit()


def _pcount(db):
    return _run(db.execute(select(func.count()).select_from(MPO))).scalar_one()


def _parent_count(db):
    return _run(db.execute(select(func.count()).select_from(PO))).scalar_one()


def _child_count(db):
    return _run(db.execute(select(func.count()).select_from(SE))).scalar_one()


# ══ FINGERPRINT ══════════════════════════════════════════════════════════════════
def test_fingerprint_is_sha256_hex():
    fp = evidence_fingerprint({"x": "y"})
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_version_is_in_payload():
    payload = {"a": "x"}
    canonical = {"fingerprint_version": FINGERPRINT_VERSION, "fields": {"a": "x"}}
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    assert evidence_fingerprint(payload) == expected
    # a different version namespace would change the hash (proves the version is IN the payload)
    other = hashlib.sha256(
        json.dumps({"fingerprint_version": FINGERPRINT_VERSION + 1, "fields": {"a": "x"}},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    assert evidence_fingerprint(payload) != other


def test_key_order_does_not_matter():
    assert evidence_fingerprint({"a": 1, "b": 2}) == evidence_fingerprint({"b": 2, "a": 1})


def test_decimal_100_equals_100_00():
    assert evidence_fingerprint({"p": Decimal("100")}) == evidence_fingerprint({"p": Decimal("100.00")})


def test_null_differs_from_zero():
    assert evidence_fingerprint({"p": None}) != evidence_fingerprint({"p": Decimal("0")})
    assert evidence_fingerprint({"p": None}) != evidence_fingerprint({"p": Decimal("0.00")})


def test_missing_fields_order_ignored():
    a = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(missing_fields=["price", "old_price"]))
    b = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                          promotion_key="__none__", fields=_pf(missing_fields=["old_price", "price"]))
    assert a == b


def test_any_semantic_change_flips_fingerprint():
    base = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                             promotion_key="__none__", fields=_pf())
    changed = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                                promotion_key="__none__", fields=_pf(buyer_price=Decimal("1400")))
    assert base != changed


def test_technical_fields_do_not_affect_fingerprint():
    # ingest_run_id / fetched_at / an internal UUID passed alongside the semantic fields are ignored:
    # price_fingerprint only reads the SEMANTIC set + resolution/kind/promotion_key.
    clean = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                              promotion_key="__none__", fields=_pf())
    noisy = price_fingerprint(resolution_status="unassigned", observation_kind="catalog",
                              promotion_key="__none__",
                              fields=_pf(ingest_run_id="RUN", fetched_at=T0, id=str(uuid.uuid4())))
    assert clean == noisy


def test_promo_child_order_ignored():
    a = promo_fingerprint(resolution_status="unassigned", fields=_promf(),
                          child_set=[["c1", "mapped"], ["c2", "unmapped"]])
    b = promo_fingerprint(resolution_status="unassigned", fields=_promf(),
                          child_set=[["c2", "unmapped"], ["c1", "mapped"]])
    assert a == b


def test_promo_child_mapping_change_flips():
    a = promo_fingerprint(resolution_status="unassigned", fields=_promf(), child_set=[["c1", "unmapped"]])
    b = promo_fingerprint(resolution_status="unassigned", fields=_promf(), child_set=[["c1", "mapped"]])
    assert a != b


# ══ observe_price — change-only ══════════════════════════════════════════════════
def test_first_observation_inserts():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    assert _pcount(db) == 1
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.fetched_at == T0 and row.last_verified_at == T0 and len(row.evidence_fingerprint) == 64


def test_unchanged_repeat_writes_no_row_and_no_bump_under_24h():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    _run(_obs_price(db, T0 + timedelta(hours=23, minutes=59)))     # < 24h
    assert _pcount(db) == 1
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.last_verified_at == T0                              # NOT bumped


def test_unchanged_repeat_bumps_last_verified_after_24h_only():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    _run(_obs_price(db, T0 + timedelta(hours=25)))                 # ≥ 24h
    assert _pcount(db) == 1                                        # still no new row
    row = _run(db.execute(select(MPO))).scalars().first()
    assert row.last_verified_at == T0 + timedelta(hours=25)        # last_verified advanced
    assert row.fetched_at == T0                                    # first-observed pinned


def test_change_writes_new_version():
    db = _run(_new_db())
    _run(_obs_price(db, T0, buyer_price=Decimal("1500")))
    _run(_obs_price(db, T0 + timedelta(hours=1), buyer_price=Decimal("1400")))
    assert _pcount(db) == 2


def test_100_90_100_keeps_three_change_points():
    db = _run(_new_db())
    _run(_obs_price(db, T0, buyer_price=Decimal("100")))
    _run(_obs_price(db, T0 + timedelta(hours=1), buyer_price=Decimal("90")))
    _run(_obs_price(db, T0 + timedelta(hours=2), buyer_price=Decimal("100")))
    # the third 100 is compared ONLY against the immediate 90, never the historical first 100.
    assert _pcount(db) == 3
    prices = sorted(r.buyer_price for r in _run(db.execute(select(MPO))).scalars().all())
    assert prices == [Decimal("90"), Decimal("100"), Decimal("100")]


def test_null_fingerprint_latest_forces_insert():
    db = _run(_new_db())
    _run(_obs_price(db, T0))
    row = _run(db.execute(select(MPO))).scalars().first()
    row.evidence_fingerprint = None                               # a hypothetical pre-fingerprint row
    _run(db.commit())
    _run(_obs_price(db, T0 + timedelta(hours=1)))                 # identical evidence
    assert _pcount(db) == 2                                       # NULL latest → never a false dedupe


# ══ observe_promotion — change-only parent + children ════════════════════════════
def test_promo_first_insert_writes_parent_and_children():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1"), ("c2", None)]))
    assert _parent_count(db) == 1 and _child_count(db) == 2


def test_promo_unchanged_child_order_no_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1"), ("c2", None)]))
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c2", None), ("c1", "st1")]))
    assert _parent_count(db) == 1 and _child_count(db) == 2       # no recreate, order-invariant


def test_promo_campaign_set_change_writes_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1")]))
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c1", "st1"), ("c2", None)]))
    assert _parent_count(db) == 2


def test_promo_mapped_to_unmapped_writes_new_version():
    db = _run(_new_db())
    _run(_obs_promo(db, T0, child_evidence=[("c1", "st1")]))       # mapped
    _run(_obs_promo(db, T0 + timedelta(hours=1), child_evidence=[("c1", None)]))   # unmapped
    assert _parent_count(db) == 2


# ══ SCHEMA GUARDS ════════════════════════════════════════════════════════════════
def test_price_series_index_exact_composition():
    idx = {i.name: [c.name for c in i.columns] for i in MPO.__table__.indexes}
    assert idx["ix_price_obs_series"] == [
        "marketplace_store_id", "external_product_id", "observation_kind",
        "promotion_key", "source", "fetched_at"]


def test_promo_series_index_exact_composition():
    idx = {i.name: [c.name for c in i.columns] for i in PO.__table__.indexes}
    assert idx["ix_promo_obs_series"] == [
        "marketplace_account_id", "external_product_id", "promotion_id", "source", "fetched_at"]


def test_last_verified_and_fingerprint_are_not_indexed():
    for model in (MPO, PO):
        for i in model.__table__.indexes:
            cols = [c.name for c in i.columns]
            assert "last_verified_at" not in cols, i.name
            assert "evidence_fingerprint" not in cols, i.name


def test_column_nullability_and_length():
    for model in (MPO, PO):
        assert model.__table__.c.last_verified_at.nullable is False
        assert model.__table__.c.evidence_fingerprint.nullable is True
        assert model.__table__.c.evidence_fingerprint.type.length == 64


# ══ INDEX EXPLAIN GATE (PostgreSQL only; SQLite EXPLAIN never substituted) ════════
def test_explain_uses_series_index_or_blocked_environment():
    url = os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")
    if not url or not (url.startswith("postgresql") or url.startswith("postgres:")):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL available; a SQLite EXPLAIN is NOT accepted as "
                    "proof of index use for the change-only latest lookup (price + promotion, incl. "
                    "product_id IS NULL). Re-run with PULT_TEST_PG_URL set against PostgreSQL.")
    import sqlalchemy as sa
    eng = sa.create_engine(url)
    try:
        with eng.begin() as c:
            Base.metadata.create_all(c)
            price_plan = "\n".join(str(r[0]) for r in c.exec_driver_sql(
                "EXPLAIN SELECT * FROM marketplace_price_observations "
                "WHERE marketplace_store_id='s' AND external_product_id='E' "
                "AND observation_kind='catalog' AND promotion_key='__none__' AND source='api' "
                "ORDER BY fetched_at DESC, created_at DESC, id DESC LIMIT 1").fetchall())
            promo_plan = "\n".join(str(r[0]) for r in c.exec_driver_sql(
                "EXPLAIN SELECT * FROM marketplace_promotion_observations "
                "WHERE marketplace_account_id='a' AND external_product_id='OF' "
                "AND promotion_id='PR' AND source='api' "
                "ORDER BY fetched_at DESC, created_at DESC, id DESC LIMIT 1").fetchall())
        assert "ix_price_obs_series" in price_plan, price_plan
        assert "ix_promo_obs_series" in promo_plan, promo_plan
    finally:
        eng.dispose()
