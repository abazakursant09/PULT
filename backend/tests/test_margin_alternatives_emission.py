"""
Sprint A2.5 — margin alternatives emission.

margin_crisis emits BOTH candidates (set_price + reduce_discount); other problems
keep a single candidate; each candidate measures on net_profit under margin_crisis.
Pure emission: no ranking, no scores, no learning, no DB writes, no Decision rows.
"""
import ast
import inspect
import uuid

from services.insight_decision_bridge import emit_candidates, DecisionCandidate
from services import insight_decision_bridge as bridge
from services.marketplace.action_metric_binding import target_metric


# ── margin_crisis emits both ─────────────────────────────────────────────────

def test_margin_crisis_emits_both_candidates():
    cands = emit_candidates("margin_crisis:wb:SKU1")
    keys = [c.action_key for c in cands]
    assert len(cands) == 3
    assert set(keys) == {"set_price", "reduce_discount", "stop_auto_promotion"}
    for c in cands:
        assert isinstance(c, DecisionCandidate)
        assert c.itype == "margin_crisis" and c.marketplace == "wb" and c.sku == "SKU1"
        assert c.insight_key == "margin_crisis:wb:SKU1"


def test_each_margin_candidate_measures_on_net_profit():
    for c in emit_candidates("margin_crisis:wb:SKU1"):
        assert target_metric(c.action_key, problem_type=c.itype) == "net_profit"


# ── other problems unchanged (single) ────────────────────────────────────────

def test_seo_opportunity_single_candidate():
    cands = emit_candidates("seo_opportunity:wb:SKU1")
    assert [c.action_key for c in cands] == ["update_card"]


def test_unmapped_problem_emits_nothing():
    assert emit_candidates("low_stock:wb:SKU1") == []
    assert emit_candidates("high_rating:wb:SKU1") == []


def test_malformed_key_safe():
    assert emit_candidates("") == []
    # itype only, no mp/sku → still emits margin actions with null context
    cands = emit_candidates("margin_crisis")
    assert {c.action_key for c in cands} == {"set_price", "reduce_discount", "stop_auto_promotion"}
    assert all(c.marketplace is None and c.sku is None for c in cands)


# ── determinism ──────────────────────────────────────────────────────────────

def test_deterministic():
    a = emit_candidates("margin_crisis:wb:SKU1")
    b = emit_candidates("margin_crisis:wb:SKU1")
    assert [(c.action_key, c.marketplace, c.sku) for c in a] == \
           [(c.action_key, c.marketplace, c.sku) for c in b]


# ── purity guards (no persistence / ranking / learning) ──────────────────────

def test_emit_is_pure_no_db_no_persistence():
    # Check the executable body only (skip the docstring, which describes the
    # constraints in prose). No persistence, no Decision creation.
    src = inspect.getsource(emit_candidates)
    body = src.split('"""', 2)[-1]  # drop the docstring
    for forbidden in ("db.add", "db.commit", "db.flush", "Decision(", ".add(", "commit("):
        assert forbidden not in body, f"emission must stay pure ({forbidden})"


def test_emit_signature_takes_no_db():
    sig = inspect.signature(emit_candidates)
    assert list(sig.parameters) == ["insight_key"]
