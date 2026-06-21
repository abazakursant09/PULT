"""
SEO A4 — deterministic Rule Engine tests.

Covers: all 12 rules can trigger; all 12 can not_trigger; not_evaluated is never
confused with not_triggered; evidence is deterministic + snapshot-only; registry
order is stable; same snapshot → same result; SEO core imports no MP clients; and
rules behave identically for WB/Ozon/Yandex snapshots.
"""
import ast
import inspect
from datetime import datetime
from pathlib import Path

from services.seo.card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)
from services.seo.evaluation import RuleResult
from services.seo.engine import evaluate_snapshot
from services.seo import rules as rules_mod
from services.seo.rules import RULE_REGISTRY

CONS = SeoConstraints(title_min_len=20, title_max_len=100, description_min_len=200,
                      media_min_images=3, attribute_fill_rate_threshold=0.6,
                      content_completeness_threshold=0.7)

# every availability key the rules can require
_ALL_FIELDS = {"title", "description", "attributes", "media", "category_schema",
               "category_path", "expected_category_path", "variants", "constraints"}


def _snap(*, mp="wildberries", title="x" * 40, description="d" * 300, brand="b",
          category_path=("root", "cat"), expected_category_path=("root", "cat"),
          schema=None, attributes=None, variants=("size",), images=4,
          availability=None) -> CardSnapshot:
    return CardSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="api", title=title, description=description, brand=brand,
        category_path=category_path, expected_category_path=expected_category_path,
        category_schema=schema if schema is not None else CategorySchema(),
        attributes=tuple(attributes or ()), variants=variants,
        media=CardMedia(image_count=images),
        constraints=CONS,
        field_availability=availability if availability is not None else {k: True for k in _ALL_FIELDS},
    )


def _by_type(results):
    return {r.problem_type: r for r in results}


# ── healthy card: nothing triggers ───────────────────────────────────────────

def _healthy_snap():
    return _snap(
        schema=CategorySchema(required_attributes=("colour",),
                              filterable_attributes=("colour",),
                              variant_attributes=("size",)),
        attributes=(CardAttribute("colour", "red", True),),
        variants=("size",),
    )


def test_healthy_card_triggers_nothing():
    res = _by_type(evaluate_snapshot(_healthy_snap()))
    assert all(r.result == RuleResult.NOT_TRIGGERED for r in res.values()), \
        {k: v.result for k, v in res.items() if v.result != RuleResult.NOT_TRIGGERED}


# ── 1. all 12 can TRIGGER ────────────────────────────────────────────────────

def test_all_rules_can_trigger():
    cases = {
        "required_attributes_missing": _snap(schema=CategorySchema(required_attributes=("colour",)),
                                             attributes=()),
        "wrong_category_placement": _snap(category_path=("a",), expected_category_path=("b",)),
        "title_too_short": _snap(title="short"),
        "title_too_long": _snap(title="x" * 200),
        "attributes_incomplete": _snap(attributes=(CardAttribute("a", None, False),
                                                   CardAttribute("b", "v", True))),  # 0.5 < 0.6
        "filter_attributes_missing": _snap(schema=CategorySchema(filterable_attributes=("colour",)),
                                           attributes=()),
        "variant_attributes_missing": _snap(schema=CategorySchema(variant_attributes=("size",)),
                                            variants=()),
        "attribute_values_invalid": _snap(attributes=(CardAttribute("a", "??", True, is_valid_format=False),)),
        "description_missing": _snap(description=""),
        "description_too_short": _snap(description="short"),
        "content_completeness_low": _snap(title="", description="", attributes=(), images=0),
        "media_below_minimum": _snap(images=0),
    }
    for ptype, snap in cases.items():
        r = _by_type(evaluate_snapshot(snap))[ptype]
        assert r.result == RuleResult.TRIGGERED, f"{ptype} expected triggered, got {r.result}/{r.reason}"
        assert r.evidence is not None and len(r.evidence) > 0
    assert set(cases) == {r.problem_type for r in RULE_REGISTRY}  # all 12 covered


# ── 2. all 12 can NOT_TRIGGER (healthy card) ─────────────────────────────────

def test_all_rules_can_not_trigger():
    res = _by_type(evaluate_snapshot(_healthy_snap()))
    for rule in RULE_REGISTRY:
        assert res[rule.problem_type].result == RuleResult.NOT_TRIGGERED


# ── 3. not_evaluated ≠ not_triggered, and carries a reason ───────────────────

def test_not_evaluated_distinct_with_reason():
    # remove specific availability → those rules become not_evaluated (not passed)
    avail = {k: True for k in _ALL_FIELDS}
    avail["category_schema"] = False        # required_attributes/filter → not_evaluated
    avail["title"] = False                  # title rules → not_evaluated
    res = _by_type(evaluate_snapshot(_snap(availability=avail)))
    for ptype in ("required_attributes_missing", "filter_attributes_missing",
                  "title_too_short", "title_too_long"):
        r = res[ptype]
        assert r.result == RuleResult.NOT_EVALUATED
        assert r.reason and "missing_fields" in r.reason
        assert r.evidence is None
    # a rule whose fields are still available is NOT not_evaluated
    assert res["media_below_minimum"].result in (RuleResult.TRIGGERED, RuleResult.NOT_TRIGGERED)


def test_predicate_level_not_evaluated():
    # expected_category_path present-but-None → predicate-level not_evaluated
    res = _by_type(evaluate_snapshot(_snap(expected_category_path=None)))
    assert res["wrong_category_placement"].result == RuleResult.NOT_EVALUATED
    assert res["wrong_category_placement"].reason == "no_expected_category_path"
    # no attributes to rate → not_evaluated (not silently passed)
    res2 = _by_type(evaluate_snapshot(_snap(attributes=())))
    assert res2["attributes_incomplete"].result == RuleResult.NOT_EVALUATED


# ── 4. evidence deterministic + snapshot-only (no MP raw payload) ─────────────

_ALLOWED_EVIDENCE_KEYS = {
    "missing_required_attributes", "required_count", "filled_count", "category_path",
    "expected_category_path", "title_length", "title_min_len", "title_max_len",
    "attribute_fill_rate", "attribute_fill_rate_threshold", "total_count",
    "missing_filter_attributes", "missing_variant_attributes", "invalid_attributes",
    "description_present", "description_length", "description_min_len",
    "content_completeness", "content_completeness_threshold", "image_count", "media_min_images",
}


def test_evidence_keys_whitelisted_and_primitive():
    snap = _snap(title="short")
    r = _by_type(evaluate_snapshot(snap))["title_too_short"]
    assert set(r.evidence) <= _ALLOWED_EVIDENCE_KEYS
    for v in r.evidence.values():
        assert isinstance(v, (int, float, str, bool, list))  # no raw objects/payloads


# ── 5. registry order stable ─────────────────────────────────────────────────

def test_registry_order_stable():
    order = [r.problem_type for r in RULE_REGISTRY]
    assert order == [r.problem_type for r in evaluate_snapshot(_healthy_snap())]
    assert len(order) == 12 and len(set(order)) == 12
    assert "keyword_cannibalization" not in order   # deliberately excluded


# ── 6. determinism ───────────────────────────────────────────────────────────

def test_same_snapshot_same_result():
    snap = _snap(title="short", images=0)
    a = evaluate_snapshot(snap)
    b = evaluate_snapshot(snap)
    assert a == b


# ── 7. SEO core imports no marketplace clients ───────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(rules_mod)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
        if "adapters" in path.parts:
            continue  # adapters are the allowed MP boundary (still empty here)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for bad in forbidden:
                    if bad in m:
                        offenders.append(f"{path.name}:{bad}")
    assert not offenders, offenders


# ── 8. identical behaviour across WB / Ozon / Yandex ─────────────────────────

def test_marketplace_agnostic_results():
    base = dict(title="short", images=0)
    results = {}
    for mp in ("wildberries", "ozon", "yandex"):
        res = _by_type(evaluate_snapshot(_snap(mp=mp, **base)))
        results[mp] = {p: r.result for p, r in res.items()}
    assert results["wildberries"] == results["ozon"] == results["yandex"]
