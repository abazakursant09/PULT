"""
Stable insight key — unit tests + action_engine guard.

Covers normalization equivalence (marketplace + sku), missing-sku policy
(:unknown + promotable=False), shape preservation, determinism, and a source
guard that every non-demo runtime key-build site routes through
build_insight_key (no inline f-string key builders remain).

COMPATIBILITY (accept-reset): normalizing the key changes the string vs the old
raw scheme; previously persisted InsightRecord.status / Telegram dedup keyed on
raw keys may reset once. Accepted at this stage; no backfill in this slice.
"""
import re
import inspect

from services.insight_keys import build_insight_key, InsightKey
from routers import action_engine


# ── Marketplace normalization ────────────────────────────────────────────────

def test_marketplace_variants_collapse_to_same_key():
    a = build_insight_key("margin_crisis", "Wildberries", "SKU1")
    b = build_insight_key("margin_crisis", "wb", "SKU1")
    c = build_insight_key("margin_crisis", "ВБ", "SKU1")
    assert a.key == b.key == c.key == "margin_crisis:wb:SKU1"
    assert a.promotable and b.promotable and c.promotable


def test_marketplace_whitespace_and_case():
    a = build_insight_key("low_stock", "  Ozon  ", "X")
    b = build_insight_key("low_stock", "ozon", "X")
    assert a.key == b.key == "low_stock:ozon:X"


# ── SKU normalization ────────────────────────────────────────────────────────

def test_sku_whitespace_and_case_collapse():
    a = build_insight_key("seo_opportunity", "wb", "  abc123 ")
    b = build_insight_key("seo_opportunity", "wb", "ABC123")
    assert a.key == b.key == "seo_opportunity:wb:ABC123"
    assert a.promotable and b.promotable


# ── Missing-sku policy ───────────────────────────────────────────────────────

def test_none_sku_is_unknown_and_not_promotable():
    r = build_insight_key("high_ad_spend", "wb", None)
    assert r.key == "high_ad_spend:wb:unknown"
    assert r.promotable is False


def test_empty_and_whitespace_sku_not_promotable():
    for sku in ("", "   ", "\t"):
        r = build_insight_key("low_stock", "wb", sku)
        assert r.key == "low_stock:wb:unknown"
        assert r.promotable is False


def test_unknown_sentinel_from_finance_aggregation_not_promotable():
    # action_engine finance dict keys null sku as the literal "unknown";
    # it must NOT become a promotable dedup anchor.
    for sku in ("unknown", "UNKNOWN", " Unknown "):
        r = build_insight_key("margin_crisis", "wb", sku)
        assert r.key == "margin_crisis:wb:unknown"
        assert r.promotable is False


# ── Shape + type ─────────────────────────────────────────────────────────────

def test_shape_is_type_marketplace_sku():
    r = build_insight_key("sales_growth", "yandex", "sku9")
    parts = r.key.split(":")
    assert len(parts) == 3
    assert parts[0] == "sales_growth"
    assert parts[1] == "yandex"
    assert parts[2] == "SKU9"


def test_returns_insightkey_namedtuple():
    r = build_insight_key("high_rating", "wb", "s")
    assert isinstance(r, InsightKey)
    assert hasattr(r, "key") and hasattr(r, "promotable")


# ── Determinism ──────────────────────────────────────────────────────────────

def test_deterministic_across_repeated_calls():
    calls = [build_insight_key("margin_crisis", "Wildberries", "  sku ") for _ in range(5)]
    assert len({c.key for c in calls}) == 1
    assert all(c.promotable for c in calls)


# ── Guard: no inline insight key builders remain in action_engine ────────────

_RUNTIME_TYPES = (
    "seo_opportunity", "high_ad_spend", "margin_crisis",
    "sales_growth", "low_stock", "high_rating",
)


def test_no_inline_fstring_key_builders_for_runtime_insights():
    src = inspect.getsource(action_engine)
    # Forbid inline f-string key construction like f"low_stock:..." for runtime
    # (non-demo) insight types. Demo keys are plain literals ("demo_..."), not
    # f-strings, so they are unaffected.
    for t in _RUNTIME_TYPES:
        pattern = r'f"' + re.escape(t) + r':'
        assert re.search(pattern, src) is None, f"inline f-string key builder for {t} still present"
    # The old shared keypart must be gone too.
    assert re.search(r':\{kp\}"', src) is None, "old '{kp}' key composition still present"


def test_all_runtime_key_sites_use_helper():
    src = inspect.getsource(action_engine)
    # 6 finance-loop sites + 1 product-only low_stock site = 7 build_insight_key calls.
    assert src.count("build_insight_key(") >= 7
