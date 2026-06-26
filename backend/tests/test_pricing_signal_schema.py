"""
A3-pre — canonical pricing contour: schema + registry wiring (no binding).

Proves the new first-class 'pricing' contour exists in the Decision Spine registries,
the pricing_signal table is created by an additive single-head migration, and NO
set_price binding / payload / executor change is introduced yet.
"""
import asyncio

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.pricing_signal import PricingSignal

from services.decision_outcome.registry import (
    CONTOURS, BY_SIGNAL_KEY, PREFIX, _DEFAULT_METRIC, _TYPES,
)
from services.decision_outcome.effect_measurement import _MODELS as EM_MODELS
from services.decision_outcome.decision_bridge import _MODELS as DB_MODELS
from services.decision_outcome.snapshot import _CONTOUR_MODELS
from services.action_binding.registry import BY_SIGNAL_TYPE, bound_signal_types


def _run(c):
    return asyncio.run(c)


# ── (1) Alembic single head ──────────────────────────────────────────────────

def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["pr1c1a2b3c4d01"], heads        # exactly one head, the pricing rev


def test_migration_is_additive_only():
    # the pricing migration only CREATEs (additive) — no drop/alter of existing tables
    import pathlib
    src = pathlib.Path("alembic/versions/pr1c1a2b3c4d01_pricing_signal_foundation.py").read_text(encoding="utf-8")
    up = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "op.create_table(" in up and "op.create_index(" in up
    for destructive in ("op.drop_table", "op.drop_column", "op.alter_column", "op.execute"):
        assert destructive not in up


# ── (2) PricingSignal table exists with the doctrine shape ───────────────────

def test_pricing_signal_table_exists():
    async def go():
        e = create_async_engine("sqlite+aiosqlite://",
                                connect_args={"check_same_thread": False}, poolclass=StaticPool)
        async with e.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            names = await conn.run_sync(lambda sync: sa_inspect(sync).get_table_names())
        assert "pricing_signal" in names
    _run(go())
    cols = {c.name for c in PricingSignal.__table__.columns}
    # five doctrine fields + lifecycle + insight_key + evidence_hash
    for required in ("what", "why", "meaning", "what_to_do", "expected_effect",
                     "insight_key", "signal_key", "status", "evidence_hash",
                     "marketplace", "sku", "user_id"):
        assert required in cols


# ── (8) pricing contour appears in the Decision Spine registry ───────────────

def test_pricing_contour_registered():
    assert "pricing" in CONTOURS
    assert PREFIX["pricing"] == "pricing"
    assert _DEFAULT_METRIC["pricing"] == "net_profit"
    assert _TYPES["pricing"] == ("negative_margin", "margin_below_target", "price_below_floor")
    for t in ("negative_margin", "margin_below_target", "price_below_floor"):
        entry = BY_SIGNAL_KEY[f"pricing_{t}"]
        assert entry.contour == "pricing"
        assert entry.default_metric_key == "net_profit"
        assert entry.three_part_compatible is True


def test_pricing_in_spine_model_maps():
    assert EM_MODELS["pricing"] is PricingSignal      # measurement contour→model
    assert DB_MODELS["pricing"] is PricingSignal      # decision bridge contour→model
    assert any(c == "pricing" and m is PricingSignal and t == "pricing_signal"
               for (c, m, t) in _CONTOUR_MODELS)      # candidate snapshot reads it


# ── (9) no set_price binding yet ─────────────────────────────────────────────

def test_pricing_binding_state():
    # A3-bind: price_below_floor → set_price (floor). A4-bind: negative_margin →
    # set_price (break-even). margin_below_target stays advice-only (no target margin).
    for t in ("price_below_floor", "negative_margin"):
        b = BY_SIGNAL_TYPE[f"pricing_{t}"]
        assert b.bindable and b.action_key == "set_price"
    mbt = BY_SIGNAL_TYPE["pricing_margin_below_target"]
    assert mbt.bindable is False and mbt.action_key is None
    assert mbt.binding_status == "no_catalog_action"
    assert len(bound_signal_types()) == 8   # 6 advertising + floor + break-even


# ── (10/11/12) no payload builder / executor / frontend change in this slice ─

def test_pricing_signal_service_stays_pure():
    # the pricing SIGNAL service (generation/reconciliation) imports no executor /
    # payload builder / apply path — it only detects, never acts. (set_price payload
    # lives in action_binding.payload_builder, the A3-bind wiring, not here.)
    import ast
    import inspect
    import services.pricing.generator as gen
    import services.pricing.signal_builder as sb
    import services.pricing.reconciliation as rec
    forbidden = ("executor", "payload_builder", "decision_apply", "action_catalog")
    for mod in (gen, sb, rec):
        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not any(f in m for m in imported for f in forbidden), (mod.__name__, imported)
