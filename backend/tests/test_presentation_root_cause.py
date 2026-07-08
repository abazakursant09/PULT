"""
Presentation Intelligence P3 — root-cause narrative.

When ≥2 diagnoses inside one RecommendationGroup converge on the SAME seller action, the
group carries a deterministic root_cause_narrative stating that observed convergence — no
invented causality, no money, no forecast. Single-diagnosis groups get None (not enough
evidence). PresentationCard.root_cause_narrative mirrors the first non-None group narrative
after P2 severity ordering.

P3 is read-layer only: card.items / card.recommendations / card.evidence order and the P2
recommendation_groups ordering are all unchanged; no diagnosis / FeedItem data altered.
"""
import uuid

from services.decision_feed.builder import FeedItem
from services.presentation.cards import build_presentation_cards, _root_cause_narrative


def _fi(contour, sku, marketplace, *, rec="сделать X", problem_type=None,
        item_key=None, severity="high"):
    it = FeedItem(
        item_key=item_key or f"{contour}_x:{marketplace}:{sku}",
        contour=contour, source_table=f"{contour}_signal", source_id=str(uuid.uuid4()),
        source_status="active", attention_state="new", marketplace=marketplace, sku=sku,
        title=f"{contour} title", what_happened="что-то", why_it_matters="почему",
        meaning="смысл", recommended_action=rec, expected_effect="эффект")
    it._priority_bucket = severity
    if problem_type is not None:
        it.source_context = {"problem_type": problem_type}
    return it


def _card_for(items):
    cards = build_presentation_cards(items)
    assert len(cards) == 1
    return cards[0]


# ── 1. group with <2 items → narrative None ──────────────────────────────────

def test_single_diagnosis_group_narrative_none():
    c = _card_for([_fi("seo", "SKU1", "wb", rec="A", problem_type="p")])
    assert len(c.recommendation_groups) == 1
    assert c.recommendation_groups[0].root_cause_narrative is None


# ── 2. group with 2+ items sharing recommendation → deterministic narrative ───

def test_converging_group_gets_narrative():
    items = [_fi("seo", "SKU1", "wb", rec="improve_listing", problem_type="title_short"),
             _fi("advertising", "SKU1", "wb", rec="improve_listing", problem_type="ad_bad_listing")]
    c = _card_for(items)
    g = c.recommendation_groups[0]
    assert g.root_cause_narrative is not None
    assert g.root_cause_narrative.startswith("2 диагнозов сходятся на одном действии «improve_listing»")
    assert g.root_cause_narrative == _root_cause_narrative(
        g.items, ["seo", "advertising"], ["title_short", "ad_bad_listing"], "improve_listing")


# ── 3. narrative includes all contributing contours verbatim ─────────────────

def test_narrative_includes_all_contours():
    items = [_fi("seo", "SKU1", "wb", rec="A", problem_type="p1"),
             _fi("advertising", "SKU1", "wb", rec="A", problem_type="p2"),
             _fi("returns", "SKU1", "wb", rec="A", problem_type="p3")]
    g = _card_for(items).recommendation_groups[0]
    for contour in ("seo", "advertising", "returns"):
        assert contour in g.root_cause_narrative


# ── 4. narrative includes all contributing problem_types verbatim ────────────

def test_narrative_includes_all_problem_types():
    items = [_fi("seo", "SKU1", "wb", rec="A", problem_type="title_short"),
             _fi("advertising", "SKU1", "wb", rec="A", problem_type="ad_bad_listing")]
    g = _card_for(items).recommendation_groups[0]
    for pt in ("title_short", "ad_bad_listing"):
        assert pt in g.root_cause_narrative


# ── 5. no invented forecast / money / causality language ─────────────────────

def test_narrative_no_forecast_money_causality():
    items = [_fi("seo", "SKU1", "wb", rec="A", problem_type="p1"),
             _fi("advertising", "SKU1", "wb", rec="A", problem_type="p2")]
    txt = _card_for(items).recommendation_groups[0].root_cause_narrative.lower()
    # no money units, no percentages, no forecast/probability verbs
    for banned in ("руб", "₽", "%", "прогноз", "вероятно", "ожидается", "может привести",
                   "приведёт", "спрогноз", "$"):
        assert banned not in txt


# ── 6. two builds produce identical narrative (deterministic) ────────────────

def test_narrative_deterministic():
    items = [_fi("returns", "SKU1", "wb", rec="A", problem_type="p2"),
             _fi("seo", "SKU1", "wb", rec="A", problem_type="p1")]
    a = build_presentation_cards(items)[0].recommendation_groups[0].root_cause_narrative
    b = build_presentation_cards(items)[0].recommendation_groups[0].root_cause_narrative
    assert a == b
    # contours + problem_types are sorted-unique → order of input items does not change text
    reordered = [_fi("seo", "SKU1", "wb", rec="A", problem_type="p1"),
                 _fi("returns", "SKU1", "wb", rec="A", problem_type="p2")]
    assert a == build_presentation_cards(reordered)[0].recommendation_groups[0].root_cause_narrative


# ── 7. card mirrors first non-None group narrative after P2 ordering ──────────

def test_card_mirrors_first_qualifying_group():
    # low-severity group "A" converges (2 items); high-severity group "B" is single (no narrative)
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low", problem_type="p1"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="low", problem_type="p2"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical", problem_type="p3")]
    c = _card_for(items)
    # P2 order: B (critical) first, then A (low). B has no narrative → card mirrors A's.
    assert [g.recommendation for g in c.recommendation_groups] == ["B", "A"]
    assert c.recommendation_groups[0].root_cause_narrative is None
    assert c.recommendation_groups[1].root_cause_narrative is not None
    assert c.root_cause_narrative == c.recommendation_groups[1].root_cause_narrative


# ── 8. card narrative None when no group qualifies ───────────────────────────

def test_card_narrative_none_when_no_convergence():
    items = [_fi("seo", "SKU1", "wb", rec="A", problem_type="p1"),
             _fi("returns", "SKU1", "wb", rec="B", problem_type="p2")]  # two singletons
    c = _card_for(items)
    assert all(g.root_cause_narrative is None for g in c.recommendation_groups)
    assert c.root_cause_narrative is None


# ── 9. card.items order unchanged ────────────────────────────────────────────

def test_card_items_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low", item_key="i1"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="low", item_key="i2"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical", item_key="i3")]
    c = _card_for(items)
    assert [it.item_key for it in c.items] == ["i1", "i2", "i3"]


# ── 10. card.recommendations order unchanged ─────────────────────────────────

def test_card_recommendations_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical")]
    c = _card_for(items)
    assert c.recommendations == ["A", "A", "B"]


# ── 11. card.evidence order unchanged ────────────────────────────────────────

def test_card_evidence_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical")]
    c = _card_for(items)
    assert [e["contour"] for e in c.evidence] == ["seo", "advertising", "returns"]


# ── 12. P2 recommendation_groups ordering unchanged ──────────────────────────

def test_p2_group_ordering_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low", problem_type="p1"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="low", problem_type="p2"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical")]
    c = _card_for(items)
    # severity still governs: critical B first, low A second — narrative did not reorder
    assert [g.recommendation for g in c.recommendation_groups] == ["B", "A"]
    assert [g.group_severity for g in c.recommendation_groups] == ["critical", "low"]


# ── 13. empty feed returns empty list ────────────────────────────────────────

def test_empty_feed_returns_empty():
    assert build_presentation_cards([]) == []
