"""
SEO A3 — CardSnapshot + SeoAdapter contract tests.

Covers: snapshot accepts WB/Ozon/Yandex identically; constraints come from the
snapshot (core cannot fabricate limits); SEO core imports no marketplace clients;
registry holds the three MPs; stub adapters degrade honestly; and the
internal_health_index is NOT a metric / NOT an Effect-PULT input.
"""
import ast
import asyncio
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from services.seo.card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)
from services.seo.adapter import SeoAdapter, SnapshotUnavailable
from services.seo import registry
from services.seo.registry import get_seo_adapter, registered_marketplaces

_CORE_DIR = Path(inspect.getfile(registry)).parent
_CONSTRAINTS = dict(title_min_len=20, title_max_len=100, description_min_len=200,
                    media_min_images=3, attribute_fill_rate_threshold=0.6,
                    content_completeness_threshold=0.7)


def _run(c):
    return asyncio.run(c)


def _snapshot(mp: str) -> CardSnapshot:
    return CardSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="api", title="t", description="d", brand="b",
        category_path=("root", "cat"), expected_category_path=("root", "cat"),
        category_schema=CategorySchema(required_attributes=("colour",)),
        attributes=(CardAttribute("colour", "red", True),),
        variants=("size",), media=CardMedia(image_count=4),
        constraints=SeoConstraints(**_CONSTRAINTS),
        field_availability={"title": True, "category_schema": True},
    )


# ── 1. WB/Ozon/Yandex accepted identically ───────────────────────────────────

def test_snapshot_accepts_all_marketplaces_identically():
    for mp in ("wildberries", "ozon", "yandex"):
        snap = _snapshot(mp)
        assert snap.marketplace == mp
        assert snap.constraints.title_min_len == 20  # same shape for every MP


# ── 2. constraints come from the snapshot, not from core ─────────────────────

def test_constraints_required_no_core_defaults():
    # SeoConstraints has NO defaults → core cannot invent limits; they must be supplied.
    with pytest.raises(TypeError):
        SeoConstraints()  # type: ignore[call-arg]
    snap = _snapshot("ozon")
    assert snap.constraints.media_min_images == 3  # value originates from the caller/adapter


# ── 3. SEO core imports no marketplace clients ───────────────────────────────

def test_seo_core_has_no_marketplace_client_imports():
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog",
                 "credential_vault")
    offenders = []
    for path in _CORE_DIR.rglob("*.py"):
        mods: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        joined = " ".join(mods)
        for bad in forbidden:
            if bad in joined:
                offenders.append(f"{path.name}:{bad}")
    assert not offenders, f"SEO core must not import marketplace clients: {offenders}"


# ── 4. registry has the three marketplaces ───────────────────────────────────

def test_registry_three_marketplaces():
    assert registered_marketplaces() == {"wildberries", "ozon", "yandex"}
    # alias resolution, no fabrication for unknown
    assert get_seo_adapter("wb").marketplace() == "wildberries"
    assert get_seo_adapter("ym").marketplace() == "yandex"
    assert get_seo_adapter("unknown_mp") is None


# ── 5. empty adapters degrade honestly (no fake data) ────────────────────────

def test_stub_adapters_degrade_honestly():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            ad = get_seo_adapter(mp)
            assert isinstance(ad, SeoAdapter)          # satisfies the Protocol
            assert ad.capabilities() == frozenset()    # nothing claimed
            res = await ad.build_snapshot(listing_id="L1")
            assert isinstance(res, SnapshotUnavailable)  # NOT a CardSnapshot, NOT fake
            assert res.reason == "adapter_not_implemented"
            assert res.marketplace == mp
    _run(go())


# ── 6. internal_health_index is not a metric / not in Effect PULT ────────────

def test_internal_health_index_not_a_metric_or_effect():
    from models.seo_audit import SeoAudit
    from sqlalchemy import inspect as sa_inspect
    cols = {c.name for c in sa_inspect(SeoAudit).columns}
    assert "internal_health_index" in cols
    assert "score" not in cols                        # public-sounding name removed

    # not registered as a measurable metric
    from services.marketplace.metric_reader import _COMPUTE_METRICS
    assert "internal_health_index" not in _COMPUTE_METRICS
    assert "seo" not in " ".join(_COMPUTE_METRICS).lower()

    # Effect PULT aggregator does not reference SEO / the health index
    from services import decision_effect_aggregator as agg
    src = inspect.getsource(agg).lower()
    assert "internal_health_index" not in src
    assert "seo_audit" not in src and "seo_signal" not in src
