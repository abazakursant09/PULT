"""
Review A4 — deterministic Rule Engine tests.

All 6 rules can trigger / not_trigger; not_evaluated stays distinct (with reason);
evidence deterministic + snapshot-only (text_excerpt only); registry order stable;
determinism; agnostic; no MP clients; no AI modules; AUTO never for RISK/ATTENTION;
5★ no text SAFE.
"""
import ast
import inspect
from datetime import datetime
from pathlib import Path

from services.review.snapshot import ReviewSnapshot
from services.review.evaluation import RuleResult
from services.review.engine import evaluate_snapshot
from services.review import rules as rules_mod
from services.review.rules import RULE_REGISTRY
from services.review.safety_policy import (
    classify_safety, OFF, MANUAL_APPROVAL, AUTO, MANUAL_ONLY, SAFE, ATTENTION, RISK,
)

T0 = datetime(2026, 6, 21)
_FIELDS = ("rating", "text", "has_text", "answered", "answer_text", "answer_created_at",
           "product_name", "brand", "category", "safety_category")


def _snap(*, mp="wildberries", rating=5, text=None, has_text=None, answered=False,
          safety_category=SAFE, allowed=(OFF, MANUAL_APPROVAL, AUTO), default=MANUAL_APPROVAL,
          availability=None):
    if has_text is None:
        has_text = bool(text and text.strip())
    return ReviewSnapshot(
        listing_id=None, marketplace=mp, sku="SKU1", captured_at=T0, source="reviews",
        review_id="R1", rating=rating, text=text, has_text=has_text, created_at=T0,
        answered=answered, answer_text=None, answer_created_at=None,
        product_name="P", brand=None, category="Кухня",
        safety_category=safety_category, allowed_modes=allowed, default_mode=default,
        field_availability=availability if availability is not None else {k: True for k in _FIELDS})


def _by(results):
    return {r.problem_type: r for r in results}


# ── 1. all 6 can TRIGGER ─────────────────────────────────────────────────────

def test_all_rules_can_trigger():
    cases = {
        "unanswered_negative_review": _snap(rating=1, safety_category=RISK,
                                            allowed=(OFF, MANUAL_ONLY), default=MANUAL_ONLY, answered=False),
        "unanswered_attention_review": _snap(rating=3, safety_category=ATTENTION,
                                             allowed=(OFF, MANUAL_APPROVAL), answered=False),
        "safe_review_can_reply": _snap(rating=5, safety_category=SAFE, answered=False),
        "five_star_without_text": _snap(rating=5, text=None, safety_category=SAFE, answered=False),
        "complaint_detected": _snap(rating=1, text="пришёл брак", safety_category=RISK,
                                    allowed=(OFF, MANUAL_ONLY), default=MANUAL_ONLY),
        "already_answered": _snap(rating=5, answered=True),
    }
    for pt, snap in cases.items():
        r = _by(evaluate_snapshot(snap))[pt]
        assert r.result == RuleResult.TRIGGERED, f"{pt}: {r.result}/{r.reason}"
        assert r.evidence and "review_id" in r.evidence
    assert set(cases) == {r.problem_type for r in RULE_REGISTRY}


# ── 2. all 6 can NOT_TRIGGER ─────────────────────────────────────────────────

def test_all_rules_can_not_trigger():
    cases = {
        "unanswered_negative_review": _snap(safety_category=SAFE),        # not RISK
        "unanswered_attention_review": _snap(safety_category=SAFE),       # not ATTENTION
        "safe_review_can_reply": _snap(safety_category=SAFE, answered=True),  # answered
        "five_star_without_text": _snap(rating=4, answered=False),        # not 5
        "complaint_detected": _snap(rating=1, text="всё хорошо", safety_category=RISK,
                                    allowed=(OFF, MANUAL_ONLY)),          # RISK but no marker
        "already_answered": _snap(answered=False),
    }
    for pt, snap in cases.items():
        assert _by(evaluate_snapshot(snap))[pt].result == RuleResult.NOT_TRIGGERED, pt


# ── 3. not_evaluated distinct, with reason ───────────────────────────────────

def test_not_evaluated_distinct():
    avail = {k: True for k in _FIELDS}
    avail.update({"safety_category": False, "answered": False})
    res = _by(evaluate_snapshot(_snap(availability=avail)))
    for pt in ("unanswered_negative_review", "unanswered_attention_review",
               "safe_review_can_reply", "already_answered"):
        assert res[pt].result == RuleResult.NOT_EVALUATED
        assert res[pt].reason and "missing_fields" in res[pt].reason
        assert res[pt].evidence is None
    # complaint needs text → not_evaluated when text unavailable
    avail2 = {k: True for k in _FIELDS}; avail2["text"] = False
    assert _by(evaluate_snapshot(_snap(availability=avail2)))["complaint_detected"].result == RuleResult.NOT_EVALUATED


# ── 4. evidence deterministic + excerpt only (no full text) ──────────────────

def test_evidence_excerpt_only():
    long_text = "пришёл брак " + "x" * 500
    r = _by(evaluate_snapshot(_snap(rating=1, text=long_text, safety_category=RISK,
                                    allowed=(OFF, MANUAL_ONLY))))["complaint_detected"]
    assert len(r.evidence["text_excerpt"]) <= 80
    assert r.evidence["complaint_markers_found"] == ["брак"]
    allowed = {"review_id", "rating", "has_text", "answered", "safety_category",
               "allowed_modes", "default_mode", "complaint_markers_found", "text_excerpt"}
    assert set(r.evidence) == allowed


# ── 5/6. registry order stable + deterministic ───────────────────────────────

def test_registry_stable_and_deterministic():
    order = [r.problem_type for r in RULE_REGISTRY]
    snap = _snap(rating=1, text="брак", safety_category=RISK, allowed=(OFF, MANUAL_ONLY))
    assert order == [r.problem_type for r in evaluate_snapshot(snap)]
    assert len(order) == 6 and len(set(order)) == 6
    assert evaluate_snapshot(snap) == evaluate_snapshot(snap)


# ── 7. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    out = {}
    for mp in ("wildberries", "ozon", "yandex"):
        snap = _snap(mp=mp, rating=1, safety_category=RISK, allowed=(OFF, MANUAL_ONLY))
        out[mp] = {p: r.result for p, r in _by(evaluate_snapshot(snap)).items()}
    assert out["wildberries"] == out["ozon"] == out["yandex"]


# ── 8. core imports no marketplace clients ───────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(rules_mod)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
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


# ── 9. no AI / generation ────────────────────────────────────────────────────

def test_no_ai_or_generation():
    core_dir = Path(inspect.getfile(rules_mod)).parent
    for path in core_dir.rglob("*.py"):
        src = path.read_text(encoding="utf-8").lower()
        for bad in ("openai", "anthropic", "llm", "generate_reply", "gpt"):
            assert bad not in src


# ── 10/11. AUTO never for RISK / ATTENTION ───────────────────────────────────

def test_auto_never_for_risk_or_attention():
    for rating in (1, 2, 3):
        d = classify_safety(rating, False, None)
        assert AUTO not in d.allowed_modes
    # engine evidence never leaks auto for RISK/ATTENTION
    for cat, allowed in ((RISK, (OFF, MANUAL_ONLY)), (ATTENTION, (OFF, MANUAL_APPROVAL))):
        snap = _snap(rating=1 if cat == RISK else 3, safety_category=cat, allowed=allowed,
                     text="брак" if cat == RISK else None)
        for r in evaluate_snapshot(snap):
            if r.evidence:
                assert AUTO not in r.evidence["allowed_modes"]


# ── 12. 5★ without text is SAFE ──────────────────────────────────────────────

def test_five_star_no_text_safe():
    assert classify_safety(5, False, None).category == SAFE
