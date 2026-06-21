"""
Legal A4 — Rule Engine tests.

Pure, read-only evaluation of a LegalSnapshot into LegalRuleEvaluationResult per
requirement candidate. Three outcomes never collapsed; missing inputs →
not_evaluated (never compliant); detected = potential risk, not a verdict;
recommended_action only from the advisory allowlist; no DB, no score/guarantee/
forecast/compliance; marketplace-agnostic.
"""
import inspect
from dataclasses import fields as dc_fields
from datetime import datetime

from services.legal.snapshot import LegalSnapshot, REQUIREMENT_CANDIDATES
from services.legal.rule_engine import (
    evaluate_snapshot, LegalResult, LegalRuleEvaluationResult, ALLOWED_ACTIONS, CLAIM_DENYLIST,
)

T0 = datetime(2026, 6, 21)
EXPECTED = ("product_certification", "trademark_usage", "labeling_requirements",
            "marketplace_offer_terms", "return_policy_obligations", "content_claim_risk")
_FA_DEFAULT = {
    "marketplace": True, "product_category": False, "product_title_or_brand": False,
    "product_text": False, "certificate_data": False, "trademark_data": False,
    "labeling_data": False, "offer_terms_data": False, "return_policy_data": False,
}


def _snap(*, mp="wildberries", content_text=None, avail=None):
    fa = dict(_FA_DEFAULT)
    if avail:
        fa.update(avail)
    return LegalSnapshot(
        seller_id="u1", marketplace=mp, subject_type="product", subject_ref="SKU1",
        sku="SKU1", listing_id=None, source="internal", snapshot_created_at=T0,
        status="not_evaluated_ready", content_text=content_text,
        available_inputs=tuple(k for k, v in fa.items() if v),
        missing_inputs=tuple(k for k, v in fa.items() if not v),
        field_availability=fa, requirement_candidates=REQUIREMENT_CANDIDATES,
        not_evaluated_reasons={})


def _by_type(snap):
    return {r.requirement_type: r for r in evaluate_snapshot(snap)}


# ── 1. all 6 candidates covered, stable order ────────────────────────────────

def test_all_candidates_covered():
    res = evaluate_snapshot(_snap())
    assert tuple(r.requirement_type for r in res) == EXPECTED
    assert all(isinstance(r, LegalRuleEvaluationResult) for r in res)


# ── 2. missing inputs → not_evaluated (never not_detected) ───────────────────

def test_missing_inputs_not_evaluated():
    ev = _by_type(_snap())   # empty everything except marketplace
    for rt in ("product_certification", "trademark_usage", "labeling_requirements",
               "marketplace_offer_terms", "return_policy_obligations", "content_claim_risk"):
        assert ev[rt].result == LegalResult.NOT_EVALUATED, rt
        assert ev[rt].reason and "missing_inputs" in ev[rt].reason


# ── 3. not_evaluated ≠ compliant ─────────────────────────────────────────────

def test_not_evaluated_not_compliant():
    ev = _by_type(_snap())
    for r in ev.values():
        blob = (str(r.evidence) + " " + (r.reason or "")).lower()
        for bad in ("compliant", "compliance", "ok", "passed", "guarantee"):
            assert bad not in blob, bad
        # not_evaluated carries a reason, never a clean pass
        if r.result == LegalResult.NOT_EVALUATED:
            assert r.reason


# ── 4. content_claim_risk detects risky claim ───────────────────────────────

def test_content_claim_detected():
    snap = _snap(content_text="Крем лечит и гарантирует 100% результат, оригинал",
                 avail={"product_text": True})
    r = _by_type(snap)["content_claim_risk"]
    assert r.result == LegalResult.DETECTED
    assert r.recommended_action == "review_content_claim"
    assert set(r.evidence["matched_keywords"]) >= {"лечит", "гарантирует", "100%", "оригинал"}
    # detected is potential risk, not a violation/verdict
    assert "potential_risk" in (r.reason or "")


# ── 5. clean content → not_detected only when text present ───────────────────

def test_clean_content_not_detected_only_with_text():
    clean = _by_type(_snap(content_text="Чайник электрический синий", avail={"product_text": True}))
    assert clean["content_claim_risk"].result == LegalResult.NOT_DETECTED
    assert clean["content_claim_risk"].evidence["matched_keywords"] == []
    # no text → not_evaluated, NOT not_detected
    notext = _by_type(_snap(content_text=None))
    assert notext["content_claim_risk"].result == LegalResult.NOT_EVALUATED


# ── 6. engine is pure (no DB / session parameter, no writes) ─────────────────

def test_engine_is_pure_no_db():
    params = set(inspect.signature(evaluate_snapshot).parameters)
    assert "db" not in params and "session" not in params
    # runs with no DB at all
    res = evaluate_snapshot(_snap())
    assert len(res) == 6


# ── 7. no score / guarantee / forecast / compliance fields ───────────────────

def test_no_score_guarantee_forecast_compliance_fields():
    names = {f.name for f in dc_fields(LegalRuleEvaluationResult)}
    for bad in ("score", "legal_score", "guarantee", "forecast", "compliance",
                "verdict", "effect_money", "expected_revenue"):
        assert bad not in names, bad


# ── 8. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    base = {rt: r.result for rt, r in _by_type(_snap(mp="wildberries")).items()}
    for mp in ("ozon", "yandex"):
        got = {rt: r.result for rt, r in _by_type(_snap(mp=mp)).items()}
        assert got == base


# ── 9. recommended_action always from the advisory allowlist ─────────────────

def test_recommended_action_allowlist():
    for content in (None, "Чайник", "лечит 100%"):
        for r in evaluate_snapshot(_snap(content_text=content, avail={"product_text": content is not None})):
            assert r.recommended_action in ALLOWED_ACTIONS
    # denylist stays minimal and cautious
    assert set(CLAIM_DENYLIST) == {"лечит", "гарантирует", "100%", "сертифицирован", "официальный", "оригинал"}
